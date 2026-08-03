# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""The `Workflow` is a `Trellis` subclass with more concrete node implementations."""

import asyncio
import functools
import json
import logging
import os
import re
import stat
import textwrap
from collections.abc import Callable, Collection, Iterator, Mapping

import attrs
from path import Path

from .cattrs import json_converter
from .constants import PLAN_PY, STEPUP_DIR
from .enums import (
    REGULAR_OUTPUT_STATES,
    TARGET_FORBIDDEN_STATES,
    FileState,
    HashUpdateCause,
    Need,
    StepState,
)
from .exceptions import GraphError
from .file import File
from .hash import FileHash, fmt_digest
from .nglob import NamedGlob, glob_base_dir, has_any_wildcards
from .path import dir_range_upper
from .sqlite3 import escape_like_pattern
from .static_tree import StaticTree
from .step import RESERVED_ENV_VARS, Step
from .trellis import Node, Root, Trellis
from .utils import string_to_bool

__all__ = ("GlobViolation", "Workflow")


logger = logging.getLogger(__name__)


# Enforce Workflow's creator-kind, dependency-kind and static-tree-ownership rules at the
# database level, as a backstop against a bug that writes directly to node/dependency
# (bypassing Trellis.create()/Node.add_source()/Node.recycle()). These are the only
# Workflow-level invariants that don't belong to a single node kind's own satellite schema
# (contrast with STEP_SCHEMA's triggers on dependency/file/node, which all maintain a
# step-table column -- see the convention comment above STEP_SCHEMA's trigger block), so
# they live here instead.
#
# A node's creator must have a kind that depends on the node's own kind:
# file <- {step, st, root}, step <- {step, root}, st <- {step}. A NULL creator
# (detached-on-creation) is always allowed and is not covered by these triggers. The root
# node is exempt (kind = 'root' in the WHEN clause): it is inserted once with creator = 1
# (self) directly in SQL (Trellis.create()), which does not fit this per-kind table.
#
# `register_static_tree`'s file <- st hand-over (`UPDATE node SET creator = ...`) is
# another deliberate direct-SQL write that bypasses `Trellis.create()`/`Node.recycle()`;
# it relies on `node_check_creator_kind_upd` below to keep enforcing this same invariant
# on that path.
WORKFLOW_SCHEMA = """
CREATE TRIGGER IF NOT EXISTS node_check_creator_kind_ins AFTER INSERT ON node
WHEN NEW.creator IS NOT NULL AND NEW.kind != 'root'
BEGIN
    SELECT RAISE(ABORT, 'invalid creator kind for new node')
    FROM node AS c
    WHERE c.i = NEW.creator
        AND NOT (
            (NEW.kind = 'file' AND c.kind IN ('step', 'st', 'root'))
            OR (NEW.kind = 'step' AND c.kind IN ('step', 'root'))
            OR (NEW.kind = 'st' AND c.kind = 'step')
        );
END;

CREATE TRIGGER IF NOT EXISTS node_check_creator_kind_upd AFTER UPDATE OF creator ON node
WHEN NEW.creator IS NOT NULL AND NEW.kind != 'root'
BEGIN
    SELECT RAISE(ABORT, 'invalid creator kind after recycle')
    FROM node AS c
    WHERE c.i = NEW.creator
        AND NOT (
            (NEW.kind = 'file' AND c.kind IN ('step', 'st', 'root'))
            OR (NEW.kind = 'step' AND c.kind IN ('step', 'root'))
            OR (NEW.kind = 'st' AND c.kind = 'step')
        );
END;

-- A dependency edge's source/sink kinds must be one of file -> step, step -> file,
-- st -> file. This also rules out self-loops, since source and sink always have
-- different kinds under this rule. Edges are only ever inserted or bulk-deleted, never
-- updated in place, and deletion cannot violate a kind-combination rule, so an _ins
-- trigger is the only one needed here.
CREATE TRIGGER IF NOT EXISTS dependency_check_kinds_ins AFTER INSERT ON dependency
BEGIN
    SELECT RAISE(ABORT, 'invalid dependency source/sink kind combination')
    FROM node AS s, node AS k
    WHERE s.i = NEW.source AND k.i = NEW.sink
        AND NOT (
            (s.kind = 'file' AND k.kind = 'step')
            OR (s.kind = 'step' AND k.kind = 'file')
            OR (s.kind = 'st' AND k.kind = 'file')
        );
END;

-- Reusable scratch tables for batch lookups keyed by a list of paths or node ids,
-- used instead of `json_each(...)`, which was found to be slow in performance tests.
-- Created once here and only ever cleared with `DELETE FROM` before reuse:
-- dropping and recreating a temp table on every call would invalidate SQLite's
-- prepared-statement cache (see `safe_update` in scheduler.py for the same convention).
CREATE TEMP TABLE IF NOT EXISTS path_list (path TEXT PRIMARY KEY) WITHOUT ROWID;
CREATE TEMP TABLE IF NOT EXISTS node_list (i INTEGER PRIMARY KEY) WITHOUT ROWID;
"""


# Find the UNCONFIRMED inputs of a step whose creator is a static tree.
# No recursion through the dependency graph is needed:
# file-to-file dependency edges no longer exist
# since directory nodes were removed from the graph (schema version 5),
# so a step's unconfirmed inputs are always among its direct inputs.
UNCONFIRMED_INPUTS = f"""
SELECT node.i, node.label FROM node
JOIN node AS cnode ON node.creator = cnode.i
JOIN file ON node.i = file.node
JOIN dependency ON node.i = source
WHERE sink = ? AND node.kind = 'file' AND file.state = {FileState.UNCONFIRMED.value}
    AND cnode.kind = 'st'
"""

# Flags the check_after bit of every step with an in-scope (declared-DEFAULT or not; see
# below) output under a directory target, for `Workflow.reconcile_targets()`. Newly-matching
# direction only -- the stale direction (a step elevated by a directory target in a previous
# run) is already covered by that method's `_implied_need = TARGET` reset.
#
# Mirrors UPDATE_CHECK_AFTER's directory arm (scheduler.py): the same dependency-based join
# (depo.source -> step), not the exact-target loop's file.creator() walk, so reconcile and
# recompute can never disagree about which step a file belongs to. No `need = DEFAULT`
# filter here -- over-flagging is safe (recomputation is state-free) and UPDATE_CHECK_AFTER
# re-applies the restriction. No GraphError arms, per the best-effort decision for directory
# targets.
#
# The CROSS JOIN is load-bearing: a plain JOIN lets SQLite pick node-first join order and
# scan every file node via node_kind_label (kind=?) alone. CROSS JOIN forces SCAN target_dir
# -> SEARCH onode USING INDEX node_kind_label (kind=? AND label>? AND label<?) -> indexed
# probes of file and dependency_sink_source.
RECONCILE_TARGET_DIRS = f"""
UPDATE step SET _check_after = 1
WHERE node IN (
    SELECT depo.source
    FROM target_dir
    CROSS JOIN node AS onode ON (
        onode.kind = '{File.kind()}'
        AND onode.label >= target_dir.path
        AND onode.label < target_dir.upper
    )
    JOIN dependency AS depo ON depo.sink = onode.i
    JOIN file AS ofile ON ofile.node = onode.i
    WHERE NOT onode.detached
      AND ofile.state != {FileState.VOLATILE.value}
)
"""

# (cause, old_state, hash_known) -> (new_state, action) for `Workflow.update_file_hashes`.
# action is one of "updated", "deleted", "completed", or None (state/hash change only).
# A missing key means the combination is unexpected and raises (see `raise_unexpected` there).
_HASH_TRANSITIONS: dict[tuple[HashUpdateCause, FileState, bool], tuple[FileState, str | None]] = {
    (HashUpdateCause.EXTERNAL, FileState.MISSING, True): (FileState.STATIC, "updated"),
    (HashUpdateCause.EXTERNAL, FileState.STATIC, True): (FileState.STATIC, "updated"),
    (HashUpdateCause.EXTERNAL, FileState.STATIC, False): (FileState.MISSING, "deleted"),
    (HashUpdateCause.EXTERNAL, FileState.BUILT, True): (FileState.AWAITED, "updated"),
    (HashUpdateCause.EXTERNAL, FileState.OUTDATED, True): (FileState.AWAITED, "updated"),
    (HashUpdateCause.EXTERNAL, FileState.BUILT, False): (FileState.AWAITED, "deleted"),
    (HashUpdateCause.EXTERNAL, FileState.OUTDATED, False): (FileState.AWAITED, "deleted"),
    (HashUpdateCause.SUCCEEDED, FileState.OUTDATED, True): (FileState.BUILT, "completed"),
    (HashUpdateCause.SUCCEEDED, FileState.AWAITED, True): (FileState.BUILT, "completed"),
    (HashUpdateCause.FAILED, FileState.STATIC, True): (FileState.STATIC, "updated"),
    (HashUpdateCause.FAILED, FileState.BUILT, True): (FileState.OUTDATED, "updated"),
    (HashUpdateCause.FAILED, FileState.OUTDATED, True): (FileState.OUTDATED, None),
    (HashUpdateCause.FAILED, FileState.AWAITED, True): (FileState.OUTDATED, None),
    (HashUpdateCause.FAILED, FileState.STATIC, False): (FileState.MISSING, "deleted"),
    (HashUpdateCause.FAILED, FileState.BUILT, False): (FileState.AWAITED, "deleted"),
    (HashUpdateCause.FAILED, FileState.OUTDATED, False): (FileState.AWAITED, None),
    (HashUpdateCause.FAILED, FileState.AWAITED, False): (FileState.AWAITED, None),
    (HashUpdateCause.CONFIRMED, FileState.UNCONFIRMED, True): (FileState.STATIC, "completed"),
    (HashUpdateCause.CONFIRMED, FileState.UNCONFIRMED, False): (FileState.MISSING, "completed"),
    # Two steps can race to be the first to use the same static-tree file: both get told to
    # check and confirm it before either confirmation is processed. The second confirmation to
    # arrive is a harmless duplicate of the first; it re-stores the hash but takes no action.
    (HashUpdateCause.CONFIRMED, FileState.STATIC, True): (FileState.STATIC, None),
    (HashUpdateCause.CONFIRMED, FileState.MISSING, False): (FileState.MISSING, None),
    # The corresponding cross-outcome races: the two confirmations disagree because the
    # file's existence changed on disk between them. Trust the later report.
    (HashUpdateCause.CONFIRMED, FileState.MISSING, True): (FileState.STATIC, "completed"),
    (HashUpdateCause.CONFIRMED, FileState.STATIC, False): (FileState.MISSING, "deleted"),
    # startup.py's scan_file_changes no longer relies on these: it confirms stray
    # UNCONFIRMED rows directly via CONFIRMED above, changed or not. Kept as a defensive
    # fallback for Watcher.watch_changes, whose EXTERNAL regen loop is not known to be
    # unreachable for a non-detached UNCONFIRMED file (Workflow.is_relevant() does not
    # exclude UNCONFIRMED, only AWAITED/VOLATILE) even though it is not expected to hit
    # one in normal operation.
    (HashUpdateCause.EXTERNAL, FileState.UNCONFIRMED, True): (FileState.STATIC, "updated"),
    (HashUpdateCause.EXTERNAL, FileState.UNCONFIRMED, False): (FileState.MISSING, "deleted"),
}


@functools.lru_cache(maxsize=1024)
def _compiled(regex: str) -> re.Pattern[str]:
    """Compile and cache a glob pattern's regex.

    The regexes come from the `nglob` table and are re-read on every check, while the
    set of distinct patterns in a project is small, so caching the compilation is what
    keeps the check cheap.
    """
    return re.compile(regex)


# The three file states a static declaration (as opposed to a build product) can leave
# behind. Shared by `_is_own_static_file` and the static-tree ownership checks in
# `_declare_file` and `register_static_tree`, so the triple cannot drift between them.
STATIC_DECLARED_STATES = (FileState.UNCONFIRMED, FileState.STATIC, FileState.MISSING)

# The three file states that mean "a step builds this". The complement of
# STATIC_DECLARED_STATES within the states a glob match can resolve to, except for
# AWAITED, which is a build product whose producer may not have run yet and is therefore
# reported as a warning, not an error (see Workflow.check_glob_matches).
BUILT_PRODUCT_STATES = (FileState.BUILT, FileState.OUTDATED, FileState.VOLATILE)


def _static_tree_file_message(tree_path: str, path: str) -> str:
    """Format the error for a static file declaration colliding with a static tree.

    Both directions produce this exact text: `_declare_file` raises it when the tree
    was declared first, `register_static_tree` when the file was. A byte-identical
    message is what makes the diagnostic independent of execution order.

    Parameters
    ----------
    tree_path
        The static tree's label, with a trailing slash.
    path
        The file inside the tree that a `static()` call tried to declare.

    Returns
    -------
    message
        The error message.
    """
    return (
        f"Static tree ({tree_path}) and static file ({path}) cannot both be declared: "
        "a static tree is the sole owner of the files under it, in either order. "
        "Drop the file declaration; use glob() to list files inside a tree."
    )


def _static_tree_product_message(tree_path: str, path: str) -> str:
    """Format the error for a build product colliding with a static tree.

    The mirror of `_static_tree_file_message` for the other collision: a step output
    or volatile output under a tree, in either declaration order.

    Parameters
    ----------
    tree_path
        The static tree's label, with a trailing slash.
    path
        The build product inside the tree.

    Returns
    -------
    message
        The error message.
    """
    return (
        f"Static tree ({tree_path}) cannot contain build product ({path}): "
        "a static tree is the sole owner of the files under it, in either order. "
        "Move the output outside the tree, or narrow the tree."
    )


@attrs.define
class SupplyInfo:
    """Result of the `_supply_files` method, for internal use only."""

    file: File = attrs.field()
    """A new or existing file."""

    state: FileState = attrs.field()
    """The state of the file when it was supplied.

    This remains valid for as long as the caller holds the `SupplyInfo`,
    because inserting a dependency edge does not affect the file state.
    (A VOLATILE file never reaches this point: `_resolve_supply_file` raises `GraphError`
    for it before a `SupplyInfo` is constructed.)
    """

    new_idep: int | None = attrs.field()
    """Dependency identifier when the relation is new, None otherwise."""

    @property
    def is_available(self) -> bool:
        """True if possibly available, False if certainly unavailable.

        If False, the file is AWAITED, OUTDATED or MISSING, and thus certainly unavailable.
        A MISSING file only becomes available again at a build boundary
        (watch phase or restart), never within the current build.
        If True, the file is BUILT, UNCONFIRMED or STATIC.
        In case of an UNCONFIRMED file, it still needs to be confirmed as STATIC (or MISSING),
        but we cannot report it as unavailable yet, hence the True value.
        """
        return self.state in (FileState.BUILT, FileState.STATIC, FileState.UNCONFIRMED)

    @property
    def is_unconfirmed(self) -> bool:
        """True if the file's state is UNCONFIRMED and its existence still needs to be checked."""
        return self.state == FileState.UNCONFIRMED


@attrs.define(frozen=True, order=True)
class GlobViolation:
    """A recorded glob match that no static declaration justifies."""

    step_label: str = attrs.field()
    """The label of the step that registered the pattern."""

    pattern: str = attrs.field()
    """The glob pattern, as registered."""

    path: str = attrs.field()
    """The offending match, relative to the root, directories with a trailing slash."""

    state: FileState | None = attrs.field(default=None, order=False)
    """The state of the attached file node, or `None` when the match has no node."""

    @property
    def is_error(self) -> bool:
        """Whether the match is a build product, i.e. an error rather than a warning."""
        return self.state in BUILT_PRODUCT_STATES


@attrs.define(eq=False)
class Workflow(Trellis):
    makedirs: bool = attrs.field(kw_only=True, default=True)
    """Whether to create parent directories of output files when they are supplied or created."""

    dir_queue: asyncio.Queue | None = attrs.field(kw_only=True)
    """Directories to be (un)watched can be added to this queue."""

    postpone_cap: int = attrs.field(kw_only=True, default=100)
    """Maximum number of consecutive postpones (since the last SUCCEEDED) before a
    step is failed instead of parked in PENDING again. A livelock guard, not expected
    to bind in normal use; see `Step.completed()`."""

    targets: frozenset[Path] = attrs.field(kw_only=True, factory=frozenset, converter=frozenset)
    """The paths `stepup build` was asked to produce.

    Set once at construction and never mutated afterward — the only way to change the
    target set is to restart the director. An empty set (the default) means
    "build everything," unchanged behavior."""

    target_dirs: frozenset[Path] = attrs.field(kw_only=True, factory=frozenset, converter=frozenset)
    """The directories `stepup build` was asked to produce everything under.

    Entries always carry their trailing slash. Set once at construction and never mutated
    afterward, mirroring `targets`. An empty set (the default) means no directory targets
    were given."""

    to_be_deleted: dict[str, FileHash | None] = attrs.field(init=False, factory=dict)
    """Files that can be deleted, plus any parent directories left empty by that.

    Maps a path to its file hash. This dict contains BUILT/OUTDATED file nodes (with their
    file hash) and VOLATILE file nodes (hash always `None`) that were removed from the graph.
    """

    @property
    def need_threshold(self) -> Need:
        """The need level above which a step's `_implied_need` makes it required."""
        return Need.DEFAULT if self.targets or self.target_dirs else Need.OPTIONAL

    #
    # Override from base class
    #

    @staticmethod
    def default_node_classes() -> list[type[Node]]:
        return [*Trellis.default_node_classes(), File, Step, StaticTree]

    @classmethod
    def schema(cls) -> str:
        """Return the SQL schema for the database, including Workflow's own triggers."""
        return super().schema() + WORKFLOW_SCHEMA

    def clean(self):
        # Get rid of static tree files that are no longer used.
        for st in self.nodes(StaticTree):
            files = sorted(st.products(), reverse=True, key=(lambda node: node.path))
            for file in files:
                if not any(file.sinks()):
                    file.detach()
        super().clean()

    def _rebuild_temp_tables(self):
        """Seed `step_need_count` once per fresh connection, then chain to the base class."""
        super()._rebuild_temp_tables()

        # step_need_count (see STEP_SCHEMA / get_counts()) is a temp table, empty on every
        # fresh connection, and only kept in sync with the step table going forward by
        # triggers. This can run more than once per connection (e.g. tests call
        # initialize() more than once), so it is unconditionally rebuilt from scratch here
        # rather than assumed empty, to stay correct (and idempotent) either way.
        self.db.execute("DELETE FROM step_need_count")
        self.db.execute(
            "INSERT INTO step_need_count (implied_need, succeeded, n) "
            "SELECT step._implied_need, step.state = ?, count(*) FROM node JOIN step "
            "ON node.i = step.node WHERE NOT node.detached GROUP BY 1, 2",
            (StepState.SUCCEEDED.value,),
        )

    def _check_consistency(self):
        """Check whether the initial graph satisfies all constraints."""
        strict = string_to_bool(os.getenv("STEPUP_DEBUG", "0"))
        super()._check_consistency()

        # Verify that all succeeded steps only have BUILT outputs.
        sql = (
            "SELECT file.state, fnode.label, snode.i, snode.label FROM node AS fnode "
            "JOIN file ON fnode.i = file.node JOIN dependency ON fnode.i = sink "
            "JOIN node AS snode ON snode.i = source JOIN step ON step.node = snode.i "
            "WHERE step.state = ? AND file.state NOT IN (?, ?) AND NOT fnode.detached"
        )
        data = (StepState.SUCCEEDED.value, FileState.BUILT.value, FileState.VOLATILE.value)
        to_mark_pending = set()
        for file_state_value, flabel, si, slabel in self.db.execute(sql, data):
            file_state = FileState(file_state_value)
            if strict:
                raise GraphError(
                    f"{file_state.name} output of succeeded step: path_out={flabel} step={slabel}"
                )
            logger.error(
                "%s output of succeeded step: path_out=%s step=%s", file_state.name, flabel, slabel
            )
            to_mark_pending.add(Step(self, si, slabel))

        # Mark steps pending to rerun steps that seem to be out of date,
        # despite being marked succeeded.
        for step in to_mark_pending:
            self.mark_step_pending(step)

    #
    # Initialization
    #

    def initialize_boot(self) -> bool:
        """Initialize the (new) boot script.

        Returns
        -------
        initialized
            Whether the boot script was (re)initialized.
        """
        command = "." / PLAN_PY
        nodes = {node.key(): node for node in self.root.products()}
        del nodes["root:"]
        if (
            len(nodes) >= 2
            and "file:plan.py" in nodes
            and nodes["file:plan.py"].get_state() == FileState.STATIC
            and f"step:{command}" in nodes
        ):
            # The boot steps are already present (from a previous invocation of stepup).
            return False

        # Need to (re)initialize the boot steps.
        for node in nodes.values():
            node.detach()
        to_check = self.declare_unconfirmed(self.root, [PLAN_PY])
        checked = {path: file_hash.regen(path) for path, file_hash in to_check.items()}
        self.update_file_hashes(checked, HashUpdateCause.CONFIRMED)
        self.define_step(self.root, command, inp_paths=[PLAN_PY], need=Need.PLAN, safe=True)
        return True

    #
    # Target reconciliation
    #

    def reconcile_targets(self):
        """Validate targets against the loaded graph and flag affected steps for recompute.

        Declaration-time validation (in `_declare_file` and `_resolve_supply_file`) only
        runs when `define_step`/`amend_step`/`declare_unconfirmed` are actually called, which
        does not happen for a database-resumed run against an unchanged `plan.py`. Call
        this once at director startup, after the boot/resume step
        (`serve`'s `Workflow.initialize_boot`/`startup_from_db`) so that a changed `plan.py`
        has already been marked `PENDING`, and before the first scheduler tick, and after
        `Scheduler.initialize()` has created and populated the `target_dir` temp table.
        It never computes elevation itself; elevation is derived, state-free
        recomputation (see `scheduler.UPDATE_CHECK_AFTER`) that runs on the next
        metadata pass for every step flagged here.

        Directory targets (`self.target_dirs`) are handled separately from `self.targets`
        below, by a single bulk range `UPDATE`. Unlike exact targets, directory-target
        elevation is best-effort and never raises: the stale direction (a step that was
        elevated by a directory target in a previous run) is already covered by the reset
        below, since such a step also has `_implied_need = TARGET`; only the newly-matching
        direction needs new code.

        Raises
        ------
        GraphError
            When an exact target matches a `VOLATILE`, `STATIC`, `MISSING` or `UNCONFIRMED`
            file whose creator chain has no `PENDING` step, i.e. the declaration producing
            that file state is not going to be re-evaluated. Never raised for directory
            targets.
        """
        # Stale TARGET values in _implied_need (left behind by a previous run with a
        # different target set) must be recomputed; over-flagging is always safe since
        # recomputation is state-free.
        self.db.execute(
            f"UPDATE step SET _check_after = 1 WHERE _implied_need = {Need.TARGET.value}"
        )
        # One-time startup cost, several queries per target instead of one batched query.
        # Accepted for now given small typical target counts; revisit if this shows up in
        # profiling.
        for path in sorted(self.targets):
            file, detached = self.find_detached(File, path)
            if file is None or detached:
                # Not (yet) in the graph, or detached. Detached rows are deliberately
                # skipped: they may be garbage from an abandoned plan, and raising on
                # those would block legitimate builds. Declaration-time checks and the
                # not-produced warning cover these.
                continue
            state = file.get_state()
            if state in TARGET_FORBIDDEN_STATES:
                # Only raise when the declaration producing this row is still current: a
                # PENDING step in the creator chain may re-declare the file differently
                # when it reruns, in which case declaration-time checks take over.
                if not self._creator_chain_pending(file):
                    self._raise_if_forbidden_target(path, state)
                continue
            creator = file.creator()
            if isinstance(creator, Step):
                self.db.execute("UPDATE step SET _check_after = 1 WHERE node = ?", (creator.i,))

        # Directory targets: newly-matching outputs need a bulk range UPDATE.
        # See RECONCILE_TARGET_DIRS above for the query and its rationale.
        self.db.execute(RECONCILE_TARGET_DIRS)

    def _creator_chain_pending(self, node: Node) -> bool:
        """Return whether any step in the node's creator chain is PENDING."""
        while True:
            node = node.creator()
            if node is None or isinstance(node, Root):
                # Root.creator() returns Root itself, so this also terminates the walk.
                return False
            if isinstance(node, Step) and node.get_state() == StepState.PENDING:
                return True

    #
    # Workflow introspection
    #

    def _format_dot_generic(self, arrowhead: str, node_sql: str, edge_sql: str) -> str:
        lines = [
            "strict digraph {",
            "  graph [rankdir=BT bgcolor=transparent]",
            "  node [penwidth=0 colorscheme=set39 style=filled fillcolor=5]",
            f"  edge [color=dimgray arrowhead={arrowhead}]",
        ]
        for i, kind, label in self.db.execute(node_sql):
            if label == "":
                label = kind
            label = json.dumps(textwrap.fill(label, 20))
            if kind == "step":
                props = ""
            elif kind == "file":
                props = " shape=rect fillcolor=9"
            elif kind == "st":
                props = " shape=octagon fillcolor=7"
            else:
                props = " shape=hexagon fillcolor=6"
            lines.append(f"  {i} [label={label}{props}]")
        for i, j in self.db.execute(edge_sql):
            lines.append(f"  {i} -> {j}")
        lines.append("}")
        return "\n".join(lines)

    def format_dot_provenance(self) -> str:
        """Return the provenance graph (creator->product) in GraphViz DOT format."""
        node_sql = "SELECT i, kind, label FROM node"
        edge_sql = "SELECT creator, i FROM node"
        return self._format_dot_generic("empty", node_sql, edge_sql)

    def format_dot_dependency(self) -> str:
        """Return the dependency graph (source-product) in GraphViz DOT format."""
        return self._format_dot_generic(
            "normal",
            "SELECT i, kind, label FROM node WHERE NOT (kind = 'root')",
            "SELECT source, sink FROM dependency "
            "JOIN node AS snode ON snode.i = source "
            "JOIN node AS cnode ON cnode.i = sink "
            "WHERE NOT ((snode.kind = 'file' AND snode.label LIKE '%/')"
            "OR (cnode.kind = 'file' AND cnode.label LIKE '%/'))",
        )

    def get_counts(self) -> tuple[int, int]:
        """Return completion counts (succeeded and total).

        Reads from `step_need_count`, a table of per-`(implied_need, succeeded)` bucket
        counts kept incrementally in sync with the `step` table by triggers (see
        `STEP_SCHEMA`), so this is a lookup over at most a handful of rows instead of a
        scan of every step in the workflow.
        """
        sql = (
            "SELECT coalesce(sum(succeeded * n), 0), coalesce(sum(n), 0) "
            "FROM step_need_count WHERE implied_need > ?"
        )
        nsucceeded, ntotal = self.db.execute(sql, (self.need_threshold.value,)).fetchone()
        return nsucceeded, ntotal

    def steps(self, state: StepState) -> Iterator[Step]:
        sql = (
            "SELECT i, label FROM node JOIN step ON node.i = step.node "
            "WHERE state = ? AND NOT detached"
        )
        for i, label in self.db.execute(sql, (state.value,)):
            yield Step(self, i, label)

    def is_regular_output(self, path: str) -> bool:
        """Return whether `path` is currently a regular (non-volatile) output of a step."""
        node, detached = self.find_detached(File, path)
        return (
            node is not None
            and not detached
            and isinstance(node.creator(), Step)
            and node.get_state() in REGULAR_OUTPUT_STATES
        )

    def dir_has_regular_output(self, path: str) -> bool:
        """Return whether any active step's regular (non-volatile) output falls under `path`.

        `path` is a directory-target label (trailing slash). Backs the end-of-build
        matched-nothing warning for directory targets (`builder.report_completion`). Unlike
        `is_regular_output()`, this checks a label range instead of a single label, and
        deliberately has no `step.need = DEFAULT` filter: the warning catches typos
        ("nothing is ever produced here"), not policy surprises ("things are produced here
        but you opted them out" -- an `OPTIONAL`-only directory is a correctly-spelled path).
        `state != VOLATILE` with a dependency-sink join mirrors the elevation arm's
        condition verbatim (`scheduler.UPDATE_CHECK_AFTER`), so warning and elevation can
        never disagree about what counts as an output.
        """
        row = self.db.execute(
            "SELECT EXISTS ("
            "SELECT 1 FROM node AS onode "
            "JOIN file AS ofile ON ofile.node = onode.i "
            "JOIN dependency AS depo ON depo.sink = onode.i "
            f"WHERE onode.kind = '{File.kind()}' "
            "AND onode.label >= ? AND onode.label < ? "
            "AND NOT onode.detached "
            f"AND ofile.state != {FileState.VOLATILE.value})",
            (path, dir_range_upper(path)),
        ).fetchone()
        return bool(row[0])

    #
    # State propagation
    #

    def update_file_hashes(self, file_hashes: Mapping[str, FileHash], cause: HashUpdateCause):
        """Update the hashes of existing files.

        Parameters
        ----------
        file_hashes
            The new hashes of the files, keyed by path.
        cause
            The reason for the hash updates.
        """
        if not isinstance(cause, HashUpdateCause):
            raise TypeError(f"cause must be a HashUpdateCause, got: {cause!r}")
        if len(file_hashes) == 0:
            return

        # Efficiently get corresponding node_index and state tuples.
        # See get_file_hashes for why this uses an IN subquery against the path_list
        # scratch table instead of a plain JOIN.
        db = self.db
        db.execute("DELETE FROM path_list")
        db.executemany("INSERT INTO path_list VALUES (?)", ((path,) for path in file_hashes))
        sql = (
            "SELECT node.i, node.label AS path, file.state FROM node "
            "JOIN file ON file.node = node.i "
            "WHERE node.kind = 'file' AND node.label IN (SELECT path FROM path_list) "
            "ORDER BY path"
        )
        records = [
            (i, path, file_hashes[path], FileState(value)) for i, path, value in db.execute(sql)
        ]

        if len(records) != len(file_hashes):
            raise AssertionError(
                f"Inconsistent number of records: expected={len(file_hashes)} actual={len(records)}"
            )

        # Files grouped by the follow-up action to take on them, keyed by the action tags used
        # in `_HASH_TRANSITIONS` (`"updated"` -> `file_externally_updated`, etc.).
        action_lists: dict[str, list[tuple[int, str]]] = {
            "updated": [],
            "deleted": [],
            "completed": [],
        }
        # Files whose state and hash must be updated.
        new_states_hashes = []

        def raise_unexpected(path, old_state, fh):
            raise AssertionError(
                f"Unexpected file hash update: cause={cause.name} path={path} "
                f"state={old_state.name} "
                f"digest={fmt_digest(fh.digest)} mode={stat.filemode(fh.mode)}"
            )

        # Decide how the file state must change and which other actions to take on the files,
        # based on the cause of the hash updates and the file's current state.
        # `new_fh` is stored as-is for every transition: the `file_clear_hash` trigger nulls
        # the hash whenever the new state is MISSING/AWAITED/VOLATILE, so there is no need to
        # special-case the stored hash for those target states here.
        for i, path, new_fh, old_state in records:
            transition = _HASH_TRANSITIONS.get((cause, old_state, not new_fh.is_unknown))
            if transition is None:
                raise_unexpected(path, old_state, new_fh)
            new_state, action = transition
            new_states_hashes.append((i, new_state, new_fh))
            if action is not None:
                action_lists[action].append((i, path))

        # Actual update of the file hashes.
        logger.info("Update file hashes: cause=%s new=%s", cause.name, new_states_hashes)
        self.db.executemany(
            "UPDATE file SET state = ?, hash = ? WHERE node = ?",
            ((state.value, fh.to_json(), i) for i, state, fh in new_states_hashes),
        )

        # Call Workflow methods to further update the workflow.
        logger.info(
            "Update file hashes: cause=%s updated=%s deleted=%s completed=%s",
            cause.name,
            action_lists["updated"],
            action_lists["deleted"],
            action_lists["completed"],
        )
        for i, path in action_lists["updated"]:
            self.file_externally_updated(File(self, i, path))
        for i, path in action_lists["deleted"]:
            self.file_externally_deleted(File(self, i, path))
        for i, path in action_lists["completed"]:
            self.mark_sinks_pending(File(self, i, path))

    def file_externally_updated(self, file: File):
        """Modify the graph to account for the external changes to this file.

        File states and hashes have already been updated before this method is called.
        """
        state = file.get_state()
        if state == FileState.STATIC:
            # Mark all sinks pending.
            for step in file.sinks(Step):
                self.mark_step_pending(step)
        elif state in (FileState.AWAITED, FileState.OUTDATED):
            # Mark the creator pending, as to make sure the file is rebuilt.
            creator = file.creator()
            if creator is not None and creator.kind() == "step":
                self.mark_step_pending(creator)

    def file_externally_deleted(self, file: File):
        """Modify the graph to account for the fact this file was deleted.

        File states and hashes have already been updated before this method is called.
        """
        state = file.get_state()
        logger.info("Externally deleted %s file: %s", state.name, file.path)

        if state == FileState.STATIC:
            file.set_state(FileState.MISSING)
            state = FileState.MISSING
        elif state in (FileState.BUILT, FileState.OUTDATED):
            file.set_state(FileState.AWAITED)
            state = FileState.AWAITED

        if state == FileState.AWAITED:
            # Request rerun of creator
            creator = file.creator()
            if creator is not None and creator.kind() == "step":
                self.mark_step_pending(creator)
        if state != FileState.VOLATILE:
            # Make all sinks pending.
            for step in file.sinks(Step):
                self.mark_step_pending(step)
            for sink_file in file.sinks(File):
                self.file_externally_deleted(sink_file)

    def mark_sinks_pending(self, file: File):
        """Mark all sink steps pending."""
        for step in file.sinks(Step, include_detached=True):
            self.mark_step_pending(step)

    def mark_step_pending(self, step: Step):
        """Set SUCCEEDED or FAILED step pending (again).

        There can be many reasons for marking a step pending again, after having been completed:

        - inputs changes
        - outputs disappeared
        - environment variables changed

        As a side effect, this method is sometimes also called on RUNNING steps,
        in which case the call is ignored.

        This method also clears the postponed flag,
        which makes the step eligible for scheduling again.
        """
        # Note that RUNNING and CHECKING are ignored.
        # This method may be called on RUNNING steps that create their own amended inputs.
        # CHECKING steps are mid hash-check and will settle naturally (SUCCEEDED or PENDING).
        state = step.get_state()
        if state in (StepState.RUNNING, StepState.CHECKING):
            return
        step.set_state(StepState.PENDING)
        if state in (StepState.SUCCEEDED, StepState.FAILED):
            logger.info("Mark %s step PENDING: %s", state.name, step.label)
            # Make all sinks (output files) pending
            for file in step.sinks(File, include_detached=True):
                if file.get_state() == FileState.BUILT:
                    self.mark_file_outdated(file)

    def mark_file_outdated(self, file: File):
        state = file.get_state()
        if state == FileState.BUILT:
            logger.info("Mark %s file OUTDATED: %s", state.name, file.path)
            file.set_state(FileState.OUTDATED)
            self.mark_sinks_pending(file)
        elif state != FileState.OUTDATED:
            raise ValueError(f"Cannot make file outdated when its state is {state.name}")

    #
    # Build phase (helper methods)
    #

    def _find_matching_static_tree(self, path: str) -> StaticTree | None:
        srs = []
        sql = (
            "SELECT i, label FROM node WHERE kind = 'st' AND NOT detached AND "
            "label = substr(?, 1, length(label))"
        )
        path = Path(path) / ""
        for i, label in self.db.execute(sql, (path,)):
            srs.append(StaticTree(self, i, label))
        if len(srs) > 1:
            raise GraphError(f"Multiple static trees match: {path}")
        if len(srs) == 1:
            return srs[0]
        return None

    def _is_own_static_file(self, creator: Node, path: str) -> bool:
        """Test whether `creator` already declared `path` as a static file.

        Parameters
        ----------
        creator
            The node re-declaring the file.
        path
            The (normalized) path of the file.

        Returns
        -------
        is_own
            Whether an attached file node for `path` exists, was created by `creator`, and
            is in a state that a static declaration can produce.
        """
        states = ", ".join(str(state.value) for state in STATIC_DECLARED_STATES)
        sql = (
            "SELECT 1 FROM node JOIN file ON file.node = node.i "
            "WHERE node.kind = 'file' AND NOT node.detached "
            "AND node.label = ? AND node.creator = ? "
            f"AND file.state IN ({states})"
        )
        return self.db.execute(sql, (path, creator.i)).fetchone() is not None

    def _raise_if_forbidden_target(self, path: str, state: FileState):
        """Raise when `path` is a build target in a state a target may never have.

        Raises
        ------
        GraphError
            When `path` is in `self.targets` and `state` is in `TARGET_FORBIDDEN_STATES`.
        """
        if path in self.targets and state in TARGET_FORBIDDEN_STATES:
            if state == FileState.VOLATILE:
                raise GraphError(f"A build target cannot be a volatile output: {path}")
            raise GraphError(f"A build target cannot be a static file: {path}")

    def _resolve_supply_file(
        self,
        node: Node,
        path: str,
        new: bool,
    ) -> tuple[File, FileState, bool]:
        """Find or create the file for a path and resolve its relation to node.

        This performs everything `_supply_files` needs except inserting the
        dependency edge, so the cyclic-dependency check can be batched
        across multiple paths by the caller.

        Parameters
        ----------
        node
            The step node to supply to.
        path
            The path of the file that should supply to the node.
        new
            When `True` the (file, node) relationship must be new.
            If not, a `GraphError` is raised.

        Returns
        -------
        file
            The existing or newly created file node.
        state
            See `SupplyInfo.state`.
        new_relation
            `True` when the (file, node) dependency edge does not exist yet
            and still needs to be inserted by the caller.

        Raises
        ------
        GraphError
            When the path is volatile.
            When the path exists while it is expected to be new.
        """
        file, detached = self.find_detached(File, path)
        if file is None or detached:
            st = self._find_matching_static_tree(path)
            if st is None:
                state = FileState.AWAITED
                file = self.create(File, None, path, state=state)
            else:
                state = FileState.UNCONFIRMED
                self._raise_if_forbidden_target(path, state)
                file = self.create(File, st, path, state=state)
            self.put_dir_queue(Path(path).parent)
        else:
            state = file.get_state()
            if state == FileState.VOLATILE:
                raise GraphError(f"Input is volatile: {path}")
            self._raise_if_forbidden_target(path, state)
        new_relation = (
            self.db.execute(
                "SELECT 1 FROM dependency WHERE source = ? AND sink = ?", (file.i, node.i)
            ).fetchone()
            is None
        )
        if not new_relation and new:
            raise GraphError(f"Supplying file already exists: {path}")
        return file, state, new_relation

    def _supply_files(
        self,
        node: Node,
        paths: Collection[str],
        new: bool = True,
    ) -> list[SupplyInfo]:
        """Find or create files for several paths and make them sources of node.

        Since `node` is the sink of every new edge in this batch,
        the cyclic-dependency check is performed once for the whole batch
        (via `Node.check_no_cycle_batch`) instead of once per path.
        Note that if `paths` contains a duplicate, it is caught later than before:
        as a `GraphError("Relation already exists")` from `add_source` instead of
        `GraphError("Supplying file already exists")`.
        This is unreachable in practice because callers already dedupe `paths`.

        Parameters
        ----------
        node
            The step node to supply to.
        paths
            The paths of the files that should supply to the node.
        new
            When `True` every (file, node) relationship must be new.
            If not, a `GraphError` is raised.

        Returns
        -------
        supply_infos
            Information about each supplied file, in the same order as `paths`.

        Raises
        ------
        GraphError
            When a path is volatile.
            When a path exists while it is expected to be new.
        CyclicError
            When adding the new relations would introduce a cyclic dependency.
        """
        resolved = [self._resolve_supply_file(node, path, new) for path in paths]
        new_file_is = [file.i for file, _, new_relation in resolved if new_relation]
        if len(new_file_is) > 0:
            node.check_no_cycle_batch(new_file_is)
        return [
            SupplyInfo(
                file,
                state,
                new_idep=(node.add_source(file, skip_cycle_check=True) if new_relation else None),
            )
            for file, state, new_relation in resolved
        ]

    def _declare_file(self, creator: Node, path: str, file_state: FileState) -> File:
        """Create (or recycle) a file with an UNCONFIRMED, AWAITED or VOLATILE file state.

        Parameters
        ----------
        creator
            The creating step or static tree.
        path
            The (normalized path). Directories must have trailing slashes.
        file_state
            The desired file state: `UNCONFIRMED`, `AWAITED` or `VOLATILE`.

        Returns
        -------
        file
            The created or recycled file node.
        """
        # Consistency checks before creating the file.
        if file_state == FileState.BUILT:
            raise ValueError("Cannot create a BUILT file. It must be AWAITED first.")
        if file_state == FileState.STATIC:
            raise ValueError("Cannot create a STATIC file. It must be UNCONFIRMED first.")
        if file_state == FileState.MISSING:
            raise ValueError("Cannot create a MISSING file. It must be UNCONFIRMED first.")
        if file_state == FileState.VOLATILE and path.endswith(os.sep):
            raise GraphError("A volatile output cannot be a directory.")
        if creator.kind() != "st":
            static_tree = self._find_matching_static_tree(path)
            if static_tree is not None:
                if file_state == FileState.UNCONFIRMED:
                    raise GraphError(_static_tree_file_message(static_tree.label, path))
                raise GraphError(_static_tree_product_message(static_tree.label, path))
        self._raise_if_forbidden_target(path, file_state)
        if path.startswith(STEPUP_DIR + os.sep):
            raise GraphError(f"Cannot declare a file under {STEPUP_DIR}: {path}")

        file = self.create(File, creator, path, state=file_state)

        if file_state == FileState.VOLATILE:
            # Do not allow volatile files to have sinks.
            if any(file.sinks()):
                raise GraphError(f"An input to an existing step cannot be volatile: {path}")
        else:
            # Watch parent directories of non-volatile files.
            self.put_dir_queue(Path(path).parent)
        return file

    def _raise_if_glob_match(self, step_label: str, paths: Collection[str]) -> None:
        """Raise when a registered glob pattern matches a path a step is about to build.

        This is the late-arriving half of the rule that a glob pattern may only match
        static files: `register_glob` catches the outputs that already exist, this
        catches the ones declared afterwards. Whichever event happens second is the one
        that raises, which is what makes the rule independent of execution order.

        Parameters
        ----------
        step_label
            The label of the step declaring `paths`, or its command when the step node
            does not exist yet (`define_step`, before the recycle short-circuit).
        paths
            The output and volatile paths the step is about to declare.
        """
        if not paths:
            return
        sql = (
            "SELECT node.i, node.label, nglob.pattern, nglob.regex FROM nglob "
            "JOIN node ON node.i = nglob.node WHERE NOT node.detached"
        )
        for _node_i, glob_step_label, pattern, regex in self.db.execute(sql):
            for path in sorted(paths):
                if _compiled(regex).fullmatch(path):
                    raise GraphError(
                        f"Glob pattern ({pattern}) registered by step ({glob_step_label}) "
                        f"matches ({path}), which step ({step_label}) builds. "
                        "A glob pattern may only match static files: narrow the pattern, "
                        "or declare the file with static() instead of building it."
                    )

    def _build_to_check(self, unconfirmed: Collection[File]) -> dict[str, FileHash]:
        """Collect the currently known hashes of UNCONFIRMED file nodes, keyed by path.

        This mapping is intended for the caller to submit as hash jobs
        (`HashQueue.submit`, `cause=HashUpdateCause.CONFIRMED`),
        one per path, so each file's validity is checked and confirmed as static
        (or resolved as missing) in the background.

        Parameters
        ----------
        unconfirmed
            UNCONFIRMED file nodes that match a static tree.

        Returns
        -------
        to_check
            The known hashes of the files to check, keyed by path, ordered by path.
        """
        db = self.db
        db.execute("DELETE FROM node_list")
        db.executemany("INSERT INTO node_list VALUES (?)", ((file.i,) for file in unconfirmed))
        sql = (
            "SELECT node.label, file.hash FROM node_list "
            "JOIN node ON node.i = node_list.i "
            "JOIN file ON file.node = node_list.i "
            "ORDER BY node.label"
        )
        return {path: FileHash.from_json(hash_value) for path, hash_value in db.execute(sql)}

    #
    # Build phase (low-level public API)
    #

    def declare_unconfirmed(self, creator: Node, paths: Collection[str]) -> dict[str, FileHash]:
        """Declare files as unconfirmed static candidates, to be confirmed shortly after.

        A file declared here becomes STATIC once confirmed present, or MISSING once
        confirmed absent, through a hash job submitted for its `to_check` entry
        (see `Workflow.update_file_hashes`).

        Parameters
        ----------
        creator
            The node creating this file.
        paths
            The locations of the files or directories (ending with /).

        Returns
        -------
        to_check
            The known hashes of the files to check, keyed by path.
            A path skipped because it is already declared static by the same creator
            contributes no entry here.

        Raises
        ------
        GraphError
            When a path lies inside a static tree owned by another creator.

        Notes
        -----
        Declaring a file that the same creator already declared static is a no-op.
        """
        if isinstance(paths, str):
            raise TypeError("The paths argument cannot be a string.")
        if creator.is_detached():
            # The creator has moved on without this call (see Step.detach()), so
            # declaring more files for it is moot.
            return {}
        # Sort paths to make the operation deterministic.
        paths = sorted(set(paths))
        own_tree_paths = []
        if creator.kind() != "st":
            # A path the same creator already declared static is a no-op, so that
            # overlapping declarations (two patterns, or a pattern and its own literal
            # match) compose instead of colliding. Its parent directory is already
            # watched by the first declaration, so put_dir_queue is not repeated here.
            # A path inside a static tree belongs to that tree, which is its sole owner.
            # The step that declared the tree may name such a path as often as it likes:
            # the declaration is handed over to the tree, so that it does not matter
            # whether the tree or the file was declared first. Any other step naming it
            # is an error, again in either order (see `register_static_tree`).
            # (`register_static_tree` calls this method with the tree itself as creator,
            # for paths *inside* the tree being registered; hence the `kind() != "st"`
            # guard here and in `_declare_file`.)
            kept = []
            for path in paths:
                static_tree = self._find_matching_static_tree(path)
                if static_tree is not None:
                    tree_creator = static_tree.creator()
                    if tree_creator is None or tree_creator.i != creator.i:
                        raise GraphError(_static_tree_file_message(static_tree.label, path))
                    own_tree_paths.append((static_tree, path))
                elif not self._is_own_static_file(creator, path):
                    kept.append(path)
            paths = kept
        # Define the files whose hashes must be checked.
        unconfirmed = [self._declare_file(creator, path, FileState.UNCONFIRMED) for path in paths]
        unconfirmed.extend(
            self._declare_file(static_tree, path, FileState.UNCONFIRMED)
            for static_tree, path in own_tree_paths
        )
        return self._build_to_check(sorted(unconfirmed, key=lambda file: file.path))

    def register_static_tree(self, creator: Node, path: str) -> dict[str, FileHash]:
        """Install a static tree.

        Parameters
        ----------
        creator
            The step creating the static tree.
        path
            A path to a directory that will be treated as a static tree.

        Returns
        -------
        to_check
            The known hashes of the matching files whose existence and validity must be
            checked, keyed by path.

        Notes
        -----
        Registering a tree that the same creator's tree already covers is a no-op.
        """
        if not isinstance(path, str):
            raise TypeError("The argument path must be a string.")
        if Path(path).isabs():
            raise ValueError(f"Static tree paths cannot be absolute paths: {path}")
        if has_any_wildcards(path):
            raise ValueError(f"Static tree does not support wildcards: {path}")
        if path == STEPUP_DIR or path.startswith(STEPUP_DIR + os.sep):
            raise GraphError(f"Cannot declare a static tree under {STEPUP_DIR}: {path}")
        if creator.is_detached():
            # The creator has moved on without this call (see Step.detach()), so
            # registering a static tree for it is moot.
            return {}
        path = Path(path) / ""
        if path in ("./", ""):
            # A root tree would have to own plan.py and every step output, which
            # defeats the point of a static tree. Reject it explicitly: without this
            # check, `_find_matching_static_tree`'s `substr(label, 1, length(label))`
            # comparison never matches an in-root label (which carries no `./` prefix),
            # so a root tree would otherwise silently own nothing and block nothing.
            raise GraphError(
                "A static tree cannot be the project root: it would have to own "
                "plan.py and every step output. Declare the subdirectories instead."
            )
        static_tree = self._find_matching_static_tree(path)
        if static_tree is not None:
            own_creator = static_tree.creator()
            if own_creator is not None and own_creator.i == creator.i:
                # This creator already covers `path` with a static tree of its own,
                # so re-registering it adds nothing.
                return {}
            raise GraphError(f"Static tree is a subdirectory of an existing static tree: {path}")
        sql = "SELECT 1 FROM node WHERE kind = 'st' AND NOT detached AND label LIKE ? ESCAPE '\\'"
        pattern = f"{escape_like_pattern(path)}%"
        if self.db.execute(sql, (pattern,)).fetchone() is not None:
            raise GraphError(
                f"Static tree is a parent directory of an existing static tree: {path}"
            )
        # A static tree is the sole owner of the files under it. Attached file nodes
        # already present under this path are therefore either this creator's own static
        # declarations, which the tree takes over below, or a violation. Which of the two
        # declarations came first only decides where the error is raised, not what it
        # says (see `_static_tree_file_message`).
        pattern = f"{escape_like_pattern(path)}%"
        sql = (
            "SELECT node.i, node.label, node.creator, file.state "
            "FROM node JOIN file ON node.i = file.node "
            "WHERE NOT node.detached AND node.label LIKE ? ESCAPE '\\' "
            "ORDER BY node.label"
        )
        handover = []
        for node_i, existing_path, existing_creator, existing_state in self.db.execute(
            sql, (pattern,)
        ):
            if existing_state not in STATIC_DECLARED_STATES:
                raise GraphError(_static_tree_product_message(path, existing_path))
            if existing_creator != creator.i:
                raise GraphError(_static_tree_file_message(path, existing_path))
            handover.append(node_i)
        st = self.create(StaticTree, creator, path)
        # The creator declared these files itself, before declaring the tree that
        # contains them. The tree is their sole owner, so transfer them to it. This is a
        # deliberate bypass of Trellis.create(): going through it would treat the
        # transfer as a recycle and call Step.lost_product() on the old creator, deleting
        # the hash of the very step that is handing them over and making it permanently
        # unskippable. Nothing is lost here -- the creator re-declares both the files and
        # the tree on its next run -- so the creator column is simply reassigned.
        for node_i in handover:
            self.db.execute("UPDATE node SET creator = ? WHERE i = ?", (st.i, node_i))
        # Adopt matching detached file nodes, e.g. leftovers from a previous run.
        # Attached nodes owned by this creator were handed over just above;
        # any other attached node raised.
        sql = (
            "SELECT label FROM node JOIN file ON node.i = file.node "
            "WHERE node.detached AND node.label LIKE ? ESCAPE '\\'"
        )
        matching_paths = [path for (path,) in self.db.execute(sql, (pattern,))]
        return self.declare_unconfirmed(st, matching_paths)

    def register_glob(self, step: Step, ng: NamedGlob) -> None:
        """Register a glob pattern used by a step and validate its matches.

        Parameters
        ----------
        step
            The step that called `glob()` (or `static()` with a pattern).
        ng
            The pattern and the matches found by the client's filesystem scan.

        Raises
        ------
        GraphError
            When a match is a known build product, or lies under the `.stepup` directory.

        Notes
        -----
        A pattern owns nothing: this only records the pattern, so the step becomes
        pending when the match set changes, and rejects matches that a glob may never
        see. Matches that are not (yet) justified are accepted here; Phase 4 catches
        the ones that never become justified.
        """
        if not isinstance(step, Step):
            raise TypeError(f"step must be a Step instance, got: {step!r}")
        if step.is_detached():
            # The step's creator has moved on without it (see Step.detach()), so
            # registering more nglobs for it is moot.
            return

        paths = ng.files()

        # Eager check (a): a match cannot already be a known build product. Detached
        # nodes are excluded: an AWAITED file with no producer is created with
        # creator=None, and Trellis.create forces detached=True in that case, so such
        # a node is not a claim that the file is a build product. The LEFT JOIN is
        # therefore only defensive here.
        if paths:
            db = self.db
            db.execute("DELETE FROM path_list")
            db.executemany("INSERT INTO path_list VALUES (?)", ((path,) for path in paths))
            sql = (
                "SELECT node.label, creator.label FROM node "
                "JOIN file ON file.node = node.i "
                "LEFT JOIN node AS creator ON creator.i = node.creator "
                "WHERE node.kind = 'file' AND NOT node.detached "
                "AND node.label IN (SELECT path FROM path_list) "
                f"AND file.state IN ({FileState.AWAITED.value}, {FileState.BUILT.value}, "
                f"{FileState.OUTDATED.value}, {FileState.VOLATILE.value}) "
                "ORDER BY node.label LIMIT 1"
            )
            row = db.execute(sql).fetchone()
            if row is not None:
                path, creator_label = row
                raise GraphError(
                    f"Glob pattern ({ng.pattern}) registered by step ({step.label}) matches "
                    f"({path}), which step ({creator_label}) builds. "
                    "A glob pattern may only match static files: narrow the pattern, "
                    "or declare the file with static() instead of building it."
                )

        # Reject matches under .stepup/, mirroring the rule _declare_file applies.
        # This is load-bearing, not defensive: NamedGlob does not skip dot entries the
        # way the standard library's glob does.
        for path in paths:
            if path.startswith(STEPUP_DIR + os.sep):
                raise GraphError(
                    f"Glob pattern ({ng.pattern}) matches a path under {STEPUP_DIR}: {path}"
                )

        step.register_glob(ng)

        # Watch the directories that could produce a new match: the parent of every
        # current match, and the pattern's base directory, so a zero-match pattern
        # still notices its first match appearing.
        for path in paths:
            self.put_glob_dir_queue(Path(path.rstrip(os.sep)).parent)
        self.put_glob_dir_queue(glob_base_dir(ng.pattern))

    def define_step(
        self,
        creator: Node,
        command: str,
        *,
        inp_paths: Collection[str] = (),
        env_deps: Collection[str] = (),
        out_paths: Collection[str] = (),
        vol_paths: Collection[str] = (),
        workdir: str = ".",
        need: Need = Need.DEFAULT,
        resources: dict[str, int] | None = None,
        safe: bool = False,
        subshell: bool = False,
        env_overrides: dict[str, str] | None = None,
        duration: float | None = None,
    ) -> dict[str, FileHash]:
        """Define a new step.

        Parameters
        ----------
        creator
            The step that generated this step.
            This is `self.root` for the boot script.
        command
            The command to execute.
        inp_paths
            Input paths.
        env_deps
            The environment variables used by the step.
        out_paths
            Output paths.
        vol_paths
            Volatile output (not reproducible) but will be cleaned like built files.
        workdir
            The directory where the command must be executed,
            typically relative to the working directory of the director.
        need
            The need of the step, see enums.Need for details.
        resources
            The resources required by the step, e.g. {"cpu": 2, "gpu": 1}.
        env_overrides
            Step-specific environment variable overrides, e.g. {"OMP_NUM_THREADS": "4"}.
            These keys must not overlap with `env_deps`.
        duration
            An initial estimate of the step's wall time in seconds, used by the scheduler to
            prioritize execution order before any measurement is available.
            When `None`, a new step gets the column's default (1.0), while a recycled step
            keeps its previously measured (or given) duration.
        safe
            The initial value for the `safe` field of the step.
            This is an internal field, not controlled by the end user.
            It is used to prevent steps from being queued if their creator is not
            RUNNING or SUCCEEDED.
            The only exception is the top-level `plan.py` step, which is always safe to queue.

        Returns
        -------
        to_check
            The known hashes of the files to check, keyed by path.
        """
        if creator.is_detached():
            # The creator has moved on without this call (see Step.detach()), so
            # defining a new child step for it is moot.
            # (This also sidesteps Node.recycle()'s "new creator must not be detached"
            # check, which Trellis.recycle() below could otherwise hit.)
            return {}

        # If it is a boot step, check that there was no boot step yet.
        if creator.i == self.root.i and any(self.root.products(Step)):
            raise GraphError("Boot step already defined.")

        # Normalize arguments
        inp_paths = sorted(set(inp_paths))
        env_deps = sorted(set(env_deps))
        out_paths = sorted(set(out_paths))
        vol_paths = sorted(set(vol_paths))
        # Deliberate duplicate of _declare_file's check: needed as long as `old_step is not
        # None` below returns early, since that path never calls _declare_file on vol_paths.
        target_vol_paths = self.targets & set(vol_paths)
        if target_vol_paths:
            raise GraphError(
                f"A build target cannot be a volatile output: {sorted(target_vol_paths)}"
            )
        if any(inp_path.endswith(os.sep) for inp_path in inp_paths):
            raise GraphError("Directory inputs are not supported.")
        if env_overrides is not None and not set(env_deps).isdisjoint(env_overrides):
            raise GraphError(
                "Variable(s) cannot be both an env dependency and a env_overrides override: "
                + ", ".join(sorted(set(env_deps) & set(env_overrides)))
            )
        if env_overrides is not None:
            reserved = set(env_overrides) & RESERVED_ENV_VARS
            if reserved:
                raise GraphError(
                    "Variable(s) set by StepUp cannot be overridden: " + ", ".join(sorted(reserved))
                )
        # This step does not exist yet, so a registered glob pattern that already matches
        # one of its outputs is named by `command` rather than by a step label. Checked
        # before the recycle short-circuit below, so it applies uniformly to a fresh
        # definition and a re-definition.
        self._raise_if_glob_match(command, out_paths + vol_paths)

        # If a compatible detached step is found, fully recycle it, instead of creating
        # a new one. This restores the step and its products (recursively), preserving
        # its edges, state and stored hash.
        old_step = self.recycle(
            Step,
            creator,
            command,
            workdir=workdir,
            need=need,
            subshell=subshell,
            resources=resources,
            env_overrides=env_overrides,
            duration=duration,
            inp_paths=inp_paths,
            env_deps=env_deps,
            out_paths=out_paths,
            vol_paths=vol_paths,
        )
        if old_step is not None:
            # Look for UNCONFIRMED inputs that match a static tree. Their existence still
            # needs to be checked, ideally confirmed by a hash job submitted for them.
            unconfirmed = {
                File(self, i, label)
                for i, label in self.db.execute(UNCONFIRMED_INPUTS, (old_step.i,))
            }
            return self._build_to_check(unconfirmed)

        # Create new step
        step = self.create(
            Step,
            creator,
            command,
            workdir=workdir,
            need=need,
            safe=safe,
            subshell=subshell,
            duration=duration,
        )
        step.set_resources(resources)
        step.set_env_overrides(env_overrides)

        # Keep track of all missing files that match a static tree and need to be confirmed.
        unconfirmed = set()

        # Supply inp_paths
        for info in self._supply_files(step, inp_paths):
            # We do not care about the unavailable files here,
            # because the step will only be executed when all inputs are available.
            if info.is_unconfirmed:
                unconfirmed.add(info.file)

        # Process vars
        step.add_env_deps(env_deps)

        # Create out_paths
        for out_path in out_paths:
            file = self._declare_file(step, out_path, FileState.AWAITED)
            file.add_source(step)

        # Create vol_paths
        for vol_path in vol_paths:
            file = self._declare_file(step, vol_path, FileState.VOLATILE)
            file.add_source(step)

        # Determine if the step needs executing and queue if relevant.
        logger.info("Define step: %s", step.label)
        return self._build_to_check(unconfirmed)

    def amend_step(
        self,
        step: Step,
        *,
        inp_paths: Collection[str] = (),
        env_deps: Collection[str] = (),
        out_paths: Collection[str] = (),
        vol_paths: Collection[str] = (),
        ran_concurrently: Callable[[int, int], bool],
    ) -> tuple[bool, set[str], set[str], dict[str, FileHash]]:
        """Amend step information.

        Parameters
        ----------
        step
            The step specifying the additional info.
        inp_paths
            Additional input paths.
        env_deps
            Additional environment variables that the step is using.
        out_paths
            Additional output paths.
        vol_paths
            Volatile output (not reproducible) but will be cleaned like built files.
        ran_concurrently
            Callable `(producer_node_i, consumer_node_i) -> bool` used to flag a `BUILT`
            input as unfresh: it decides whether the producer step's execution window
            overlapped the current step's, meaning the current step may have read stale
            content. Only ever `Scheduler.ran_concurrently`, passed by the `amend()` RPC
            handler.

        Returns
        -------
        is_detached
            `True` if the step is detached and its amendments are moot.
        unavailable
            A set of input paths that are not available.
        unfresh
            A set of input paths that are available but fail the amend() freshness check.
        to_check
            The known hashes, keyed by path, of the files whose validity must still be checked,
            e.g. by submitting a hash job for each (`cause=HashUpdateCause.CONFIRMED`). A path that
            resolves to MISSING must then move from `unavailable`'s absence to its presence,
            i.e. the caller must join it into `unavailable` after checking.
        """
        if not isinstance(step, Step):
            raise TypeError(f"step must be a Step instance, got: {step!r}")
        if step.is_detached():
            # The step's creator has moved on without it (see Step.detach()), so
            # its amendments are moot.
            return True, set(), set(), {}

        # Normalize arguments
        inp_paths = sorted(set(inp_paths))
        out_paths = sorted(set(out_paths))
        vol_paths = sorted(set(vol_paths))
        if any(inp_path.endswith(os.sep) for inp_path in inp_paths):
            raise GraphError("Directory inputs are not supported.")

        # Keep track of missing files, of which there are three different types:
        # - unavailable = certainly not available
        # - unfresh = available, but fails the amend() freshness check.
        # - unconfirmed = possibly available but need to be checked.
        #   These are UNCONFIRMED files that need to be confirmed as STATIC (or MISSING).
        unavailable = set()
        unfresh = set()
        unconfirmed = set()
        amended_ideps = []

        # Process inp_paths
        infos = self._supply_files(step, inp_paths, new=False)
        for info in infos:
            if not info.is_available:
                unavailable.add(info.file.path)
            elif info.state == FileState.BUILT:
                producer = info.file.creator()
                if isinstance(producer, Step) and ran_concurrently(producer.i, step.i):
                    unfresh.add(info.file.path)
            if info.new_idep is not None:
                amended_ideps.append((info.new_idep,))
            if info.is_unconfirmed:
                unconfirmed.add(info.file)

        # Process vars
        step.amend_env_deps(env_deps)

        self._raise_if_glob_match(step.label, out_paths + vol_paths)

        # Create out_paths
        for out_path in out_paths:
            file = self._declare_file(step, out_path, FileState.AWAITED)
            new_idep = file.add_source(step)
            amended_ideps.append((new_idep,))

        # Create vol_paths
        for vol_path in vol_paths:
            file = self._declare_file(step, vol_path, FileState.VOLATILE)
            new_idep = file.add_source(step)
            amended_ideps.append((new_idep,))

        self.db.executemany("INSERT INTO amended_dep VALUES (?)", amended_ideps)
        return False, unavailable, unfresh, self._build_to_check(unconfirmed)

    #
    # Watch phase
    #

    def matches_any_glob(self, path: str) -> bool:
        """Test whether any registered glob pattern matches `path`.

        This is the relevance test for a path with no node of its own: after a pattern
        stopped declaring its matches, the pattern itself is the only record that the
        path is interesting. Deliberately not `NamedGlob.may_change`, which answers a
        different question (it discards a path that is already a recorded match, so it
        cannot see a deletion) and deserializes every match set to do it.
        """
        sql = (
            "SELECT nglob.regex FROM nglob JOIN node ON node.i = nglob.node WHERE NOT node.detached"
        )
        return any(_compiled(regex).fullmatch(path) for (regex,) in self.db.execute(sql))

    def is_relevant(self, path: str) -> bool:
        file, detached = self.find_detached(File, path)
        if not (file is None or detached):
            return file.get_state() not in (FileState.AWAITED, FileState.VOLATILE)
        return self.matches_any_glob(path)

    def is_relevant_during_build(self, path: str) -> bool:
        """Relevance test for events observed while a build phase was running.

        Stricter than `is_relevant`: a file the build itself is writing is not a user
        edit, so only `STATIC` and `MISSING` nodes qualify. A path with no node at all
        is judged by the registered glob patterns, as in `is_relevant`; no step can be
        building it, since a pattern may not match a build product.
        """
        file, detached = self.find_detached(File, path)
        if not (file is None or detached):
            return file.get_state() in (FileState.STATIC, FileState.MISSING)
        return self.matches_any_glob(path)

    def relevant_paths(self, parent: str) -> Iterator[str]:
        """Iterate over all paths under `parent` whose disappearance is relevant.

        Both file nodes and the recorded matches of glob patterns are considered: a
        match has no node of its own, so the pattern is the only place it is recorded.
        """
        seen = set()
        sql = (
            "SELECT label FROM node JOIN file ON node.i = file.node "
            f"WHERE state NOT IN ({FileState.AWAITED.value}, {FileState.VOLATILE.value}) AND "
            "node.label LIKE ? AND NOT detached"
        )
        pattern = f"{escape_like_pattern(parent)}%"
        for (path,) in self.db.execute(sql, (pattern,)):
            seen.add(path)
            yield path
        for ng in self.nglobs():
            for path in ng.files():
                if path.startswith(parent) and path not in seen:
                    seen.add(path)
                    yield path

    def nglobs(self, yield_step: bool = False) -> Iterator[NamedGlob | tuple[int, NamedGlob, Step]]:
        sql = (
            "SELECT node.i, label, kind, nglob.i, data FROM node "
            "JOIN nglob ON node.i = nglob.node WHERE NOT node.detached"
        )
        for node_i, label, kind, nglob_i, data in self.db.execute(sql):
            if kind != "step":
                raise ValueError("Only steps can define nglobs")
            ng = json_converter.structure(json.loads(data), NamedGlob)
            yield (nglob_i, ng, Step(self, node_i, label)) if yield_step else ng

    def check_glob_matches(self) -> list[GlobViolation]:
        """Find recorded glob matches that no static declaration justifies.

        Runs once at the end of every build phase, over the persisted `nglob` table
        rather than over the patterns registered during this phase, which makes it
        order-independent and idempotent across restarts.
        See `finalize.report_completion` for the reporting side.

        Returns
        -------
        violations
            The unjustified matches, sorted by (step label, pattern, path).
        """
        records = []
        paths = set()
        for _nglob_i, ng, step in self.nglobs(yield_step=True):
            for path in ng.files():
                records.append((step.label, ng.pattern, str(path)))
                paths.add(str(path))
        if len(records) == 0:
            return []

        # Resolve every match against the attached file nodes in one bulk query.
        # See get_file_hashes for why this uses an IN subquery against path_list.
        db = self.db
        db.execute("DELETE FROM path_list")
        db.executemany("INSERT INTO path_list VALUES (?)", ((path,) for path in paths))
        sql = (
            "SELECT node.label, file.state FROM node "
            "JOIN file ON file.node = node.i "
            "WHERE node.kind = 'file' AND NOT node.detached "
            "AND node.label IN (SELECT path FROM path_list)"
        )
        states = {path: FileState(value) for path, value in db.execute(sql)}

        # Static tree roots are few, so a prefix test in Python beats a join per match.
        tree_labels = [
            label
            for (label,) in db.execute("SELECT label FROM node WHERE kind = 'st' AND NOT detached")
        ]

        violations = []
        for step_label, pattern, path in records:
            state = states.get(path)
            if state is not None:
                if state in STATIC_DECLARED_STATES:
                    continue
            elif self._is_justified_without_node(path, tree_labels) or not Path(path).exists():
                # A match deleted during the build is not the user's fault to fix:
                # the watcher and the startup rescan already handle its disappearance.
                continue
            violations.append(GlobViolation(step_label, pattern, path, state))
        return sorted(violations)

    def _is_justified_without_node(self, path: str, tree_labels: list[str]) -> bool:
        """Test whether a match with no file node of its own is nevertheless justified.

        Parameters
        ----------
        path
            The match, root-relative, directories with a trailing separator.
        tree_labels
            The labels of all attached static trees, each with a trailing separator.

        Returns
        -------
        justified
            Whether `path` is (inside) a static tree, or a directory that contains a
            static tree or a static file.
        """
        # A) Inside a static tree, or a static tree root itself.
        # Appending a separator reproduces _find_matching_static_tree's `Path(path) / ""` exactly.
        probe = path if path.endswith(os.sep) else path + os.sep
        if any(probe.startswith(label) for label in tree_labels):
            return True
        if not path.endswith(os.sep):
            return False
        # B) Directory-only arms: the match contains a static tree, or a static file.
        # A root match ("./") contains every label, which the prefix range cannot express:
        # labels are root-relative and carry no "./" prefix,
        # so dir_range_upper("./") == ".0" would match nothing.
        # Drop the range instead of inventing a sentinel upper bound
        # (SQLite compares TEXT byte-wise by default, so no string is a reliable maximum).
        is_root = path in ("./", "/")
        if any(is_root or label.startswith(path) for label in tree_labels):
            return True
        states = ", ".join(str(state.value) for state in STATIC_DECLARED_STATES)
        sql = (
            "SELECT 1 FROM node JOIN file ON file.node = node.i "
            "WHERE node.kind = 'file' AND NOT node.detached "
            f"AND file.state IN ({states}) "
        )
        args = ()
        if not is_root:
            sql += "AND node.label >= ? AND node.label < ? "
            args = (path, dir_range_upper(path))
        return self.db.execute(sql + "LIMIT 1", args).fetchone() is not None

    def process_nglob_changes(self, deleted: Collection[str], added: Collection[str]):
        """Mark steps with nglob pending if they are affected by the deleted and updated paths.

        Parameters
        ----------
        deleted
            The deleted files.
        added
            The added files.
        """
        if deleted & added:
            raise ValueError("Deleted and added paths cannot overlap.")
        for i, ng, step in self.nglobs(yield_step=True):
            # Check if any of the deleted files matches an nglob.
            # If yes, step becomes pending.
            # Check if added files could result in new nglob matches.
            # If yes, step becomes pending.
            evolved = ng.will_change(deleted, added)
            if evolved is not None:
                step.delete_hash()
                data = (json.dumps(json_converter.unstructure(evolved)), i)
                self.db.execute("UPDATE nglob SET data = ? WHERE i = ?", data)
                self.mark_step_pending(step)

    def get_file_hashes(self, paths: Collection[str]) -> dict[str, FileHash]:
        """Get the hashes of existing files.

        Parameters
        ----------
        paths
            A list of paths.

        Returns
        -------
        file_hashes
            The current hashes of the files, keyed by path, ordered by path.
        """
        # The `label IN (SELECT path FROM path_list)` form makes the planner drive from
        # `node`'s `node_kind_label` index (probed once per requested path via a Bloom-filtered
        # membership test), instead of a plain JOIN against path_list, which lets the planner
        # drive from a full scan of `node` instead, an O(n_nodes) cost regardless of how few
        # paths are requested. As a bonus, results come out pre-sorted by the covering index,
        # so no separate ORDER BY sort is needed. `path_list` is a real indexed scratch table
        # (see file.py), populated here and cleared before reuse, instead of `json_each(...)`,
        # which was found to be slow in performance tests.
        db = self.db
        db.execute("DELETE FROM path_list")
        db.executemany("INSERT INTO path_list VALUES (?)", ((path,) for path in paths))
        sql = (
            "SELECT node.label, file.hash FROM node "
            "JOIN file ON file.node = node.i "
            "WHERE node.kind = 'file' AND node.label IN (SELECT path FROM path_list) "
            "ORDER BY node.label"
        )
        return {path: FileHash.from_json(hash_value) for path, hash_value in db.execute(sql)}

    def put_dir_queue(self, path: str):
        """Put a directory in the dir_queue, with some consistency checks."""
        path = Path(path)
        if path == "":
            path = Path(".")
        if self.makedirs:
            path.makedirs_p()
        if self.dir_queue is not None:
            self.dir_queue.put_nowait(path)

    def put_glob_dir_queue(self, path: str):
        """Watch the nearest existing ancestor of `path`, without creating directories.

        Unlike `put_dir_queue`, this never calls `makedirs_p`: a glob pattern only
        observes the filesystem, so registering one must not create the directory it
        points at. When the directory does not exist, the closest existing ancestor is
        watched instead, which is the best that can be done without creating anything.
        """
        path = Path(path) if path else Path(".")
        while path not in ("", ".") and not path.is_dir():
            path = path.parent
        self.put_dir_queue(path if path != "" else ".")
