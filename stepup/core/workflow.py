# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""The `Workflow` is a `Trellis` subclass with more concrete node implementations."""

import asyncio
import json
import logging
import os
import pickle
import stat
import textwrap
from collections.abc import Callable, Collection, Iterator

import attrs
from path import Path

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
from .nglob import NGlobMulti, has_wildcards
from .path import dir_range_upper
from .sqlite3 import escape_like_pattern
from .static_tree import StaticTree
from .step import RESERVED_ENV_VARS, Step
from .trellis import Node, Root, Trellis
from .utils import string_to_bool

__all__ = ("Workflow",)


logger = logging.getLogger(__name__)


# Find the UNCONFIRMED inputs of a step whose creator is a static tree.
# No recursion through the dependency graph is needed:
# file-to-file dependency edges no longer exist
# since directory nodes were removed from the graph (schema version 5),
# so a step's deferred inputs are always among its direct inputs.
DEFERRED_INPUTS = f"""
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
    # Only the changed/deleted flavors of crash recovery for a stray UNCONFIRMED file
    # are handled here; the unchanged flavor goes through a step rerun instead
    # (see startup.py: scan_file_changes and the RUNNING -> FAILED reset).
    (HashUpdateCause.EXTERNAL, FileState.UNCONFIRMED, True): (FileState.STATIC, "updated"),
    (HashUpdateCause.EXTERNAL, FileState.UNCONFIRMED, False): (FileState.MISSING, "deleted"),
}


@attrs.define
class SupplyInfo:
    """Result of the `_supply_files` method, for internal use only."""

    file: Node = attrs.field()
    """A new or existing file."""

    available: bool = attrs.field()
    """True if possibly available, False if the certainly unavailable.

    If False, the file is AWAITED, OUTDATED, VOLATILE or MISSING, and thus certainly unavailable.
    A MISSING file only becomes available again at a build boundary
    (watch phase or restart), never within the current build.
    If True, the file is BUILT, UNCONFIRMED or STATIC.
    In case of an UNCONFIRMED file, it still needs to be confirmed as STATIC (or MISSING),
    but we cannot report it as unavailable yet, hence the True value.
    """

    is_deferred: bool = attrs.field()
    """True if the file attribute is UNCONFIRMED and needs to be checked."""

    new_idep: int | None = attrs.field()
    """Dependency identifier when the relation is new, None otherwise."""


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

    to_be_deleted: list[tuple[str, FileHash | None]] = attrs.field(init=False, factory=list)
    """A list of files and directories that can be deleted.

    This list contains BUILT files node with file hashes that were removed from the graph.
    """

    @property
    def need_threshold(self) -> Need:
        """The need level above which a step's `_implied_need` makes it required."""
        return Need.DEFAULT if self.targets or self.target_dirs else Need.OPTIONAL

    @staticmethod
    def default_node_classes() -> list[type[Node]]:
        return [*Trellis.default_node_classes(), File, Step, StaticTree]

    #
    # Initialization
    #

    def check_consistency(self):
        """Check whether the initial graph satisfies all constraints."""
        strict = string_to_bool(os.getenv("STEPUP_DEBUG", "0"))
        super().check_consistency()

        # Verify that all BUILT, OUTDATED and STATIC files have a hash.
        sql = (
            "SELECT i, state, label FROM node JOIN file ON node.i = file.node "
            "WHERE state IN (?, ?, ?) and hash IS NULL"
        )
        data = (FileState.BUILT.value, FileState.OUTDATED.value, FileState.STATIC.value)
        files = []
        file_hashes = []
        for i, file_state_value, path in self.db.execute(sql, data):
            file_state = FileState(file_state_value)
            if strict:
                raise GraphError(f"{file_state.name} file without hash: {path}")
            logger.error(f"{file_state.name} file without hash: %s", path)
            files.append(File(self, i, path))
            file_hashes.append((path, FileHash.unknown().regen(path)))
        if len(file_hashes) > 0:
            logger.error("Fixing %s file hashes", len(file_hashes))
            self.update_file_hashes(file_hashes, HashUpdateCause.EXTERNAL)
            for file in files:
                self.mark_file_outdated(file)

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

    def initialize_boot(self) -> bool:
        """Initialize the (new) boot script.

        Returns
        -------
        initialized
            Whether the boot script was (re)initialized.
        """
        command = "." / Path("plan.py")
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
        to_check = self.declare_unconfirmed(self.root, ["plan.py"])
        checked = [(path, file_hash.regen(path)) for path, file_hash in to_check]
        self.update_file_hashes(checked, HashUpdateCause.CONFIRMED)
        self.define_step(self.root, command, inp_paths=["plan.py"], need=Need.PLAN, safe=True)
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

    def get_file_counts(self) -> dict[FileState, int]:
        """Return counters for FileState."""
        sql = (
            "SELECT file.state, count(*) FROM node JOIN file ON node.i = file.node "
            "WHERE NOT node.detached GROUP BY file.state"
        )
        return {FileState(value): count for value, count in self.db.execute(sql)}

    def get_step_counts(self) -> dict[StepState, int]:
        """Return counters for StepState."""
        sql = (
            "SELECT step.state, count(*) FROM node JOIN step ON node.i = step.node "
            "WHERE NOT node.detached GROUP BY step.state"
        )
        return {StepState(value): count for value, count in self.db.execute(sql)}

    def steps(self, state: StepState) -> Iterator[Step]:
        sql = (
            "SELECT i, label FROM node JOIN step ON node.i = step.node "
            "WHERE state = ? AND NOT detached"
        )
        for i, label in self.db.execute(sql, (state.value,)):
            yield Step(self, i, label)

    def detached_inp_paths(self) -> Iterator[tuple[str, FileState]]:
        """Iterate over detached input paths used by non-detached steps."""
        sql = (
            "SELECT node.label, file.state FROM node JOIN file ON node.i = file.node "
            "WHERE node.detached "
            "AND EXISTS (SELECT 1 FROM dependency JOIN node ON node.i = dependency.sink "
            "WHERE source = file.node AND not node.detached)"
        )
        for row in self.db.execute(sql):
            yield row[0], FileState(row[1])

    def missing_paths(self) -> Iterator[str]:
        """Iterate over static files that are confirmed absent (deleted or never present)."""
        sql = (
            "SELECT label FROM node JOIN file ON node.i = file.node "
            "WHERE state = ? AND NOT detached"
        )
        for row in self.db.execute(sql, (FileState.MISSING.value,)):
            yield row[0]

    #
    # Trellis extensions
    #

    def _check_creator(self, node_type: type[Node], creator: Node | None) -> None:
        super()._check_creator(node_type, creator)
        if creator is None or node_type is Root:
            return
        if (
            (node_type == File and not isinstance(creator, (Step, StaticTree, Root)))
            or (node_type == Step and not isinstance(creator, (Step, Root)))
            or (node_type == StaticTree and not isinstance(creator, Step))
        ):
            raise GraphError(
                f"Cannot create {node_type.__name__} with creator {creator.key()!r}: "
                "creator must be a step or static tree"
            )

    def _check_source(self, source: Node, sink: Node) -> None:
        super()._check_source(source, sink)
        if (
            (isinstance(source, File) and not isinstance(sink, Step))
            or (isinstance(source, Step) and not isinstance(sink, File))
            or (isinstance(source, StaticTree) and not isinstance(sink, File))
        ):
            raise GraphError(
                f"Node {sink.key()!r} (kind={sink.kind()!r}) cannot be a dependency sink"
            )

    def clean(self):
        # Get rid of static tree files that are no longer used.
        for st in self.nodes(StaticTree):
            files = sorted(st.products(), reverse=True, key=(lambda node: node.path))
            for file in files:
                if not any(file.sinks()):
                    file.detach()
        super().clean()

    #
    # State propagation
    #

    def update_file_hashes(
        self, file_hashes: Collection[tuple[str, FileHash]], cause: HashUpdateCause
    ):
        """Update the hashes of existing files.

        Parameters
        ----------
        file_hashes
            A list of `(path, file_hash)` tuples.
        cause
            The reason for the hash updates.
        """
        if not isinstance(cause, HashUpdateCause):
            raise TypeError(f"cause must be a HashUpdateCause, got: {cause!r}")
        if len(file_hashes) == 0:
            return

        # Efficiently get corresponding node_index and state tuples.
        file_hashes = dict(file_hashes)
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
        if len(new_states_hashes) != len(file_hashes):
            raise AssertionError(
                f"Inconsistent number of file hash updates: "
                f"expected={len(file_hashes)} actual={len(new_states_hashes)}"
            )
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

    def _matching_static_tree(self, path: str) -> StaticTree | None:
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
    ) -> tuple[File, bool, bool, bool]:
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
        available
            See `SupplyInfo.available`.
        is_deferred
            See `SupplyInfo.is_deferred`.
        new_relation
            `True` when the (file, node) dependency edge does not exist yet
            and still needs to be inserted by the caller.

        Raises
        ------
        GraphError
            When the path is volatile.
            When the path exists while it is expected to be new.
        """
        available = False
        file, detached = self.find_detached(File, path)
        is_deferred = False
        if file is None or detached:
            st = self._matching_static_tree(path)
            if st is None:
                file = self.create(File, None, path, state=FileState.AWAITED)
            else:
                self._raise_if_forbidden_target(path, FileState.UNCONFIRMED)
                file = self.create(File, st, path, state=FileState.UNCONFIRMED)
                is_deferred = True
                available = True
            self.put_dir_queue(Path(path).parent)
        else:
            state = file.get_state()
            if state == FileState.VOLATILE:
                raise GraphError(f"Input is volatile: {path}")
            self._raise_if_forbidden_target(path, state)
            available = state in (FileState.BUILT, FileState.STATIC, FileState.UNCONFIRMED)
            if state == FileState.UNCONFIRMED:
                is_deferred = True
        new_relation = (
            self.db.execute(
                "SELECT 1 FROM dependency WHERE source = ? AND sink = ?", (file.i, node.i)
            ).fetchone()
            is None
        )
        if not new_relation and new:
            raise GraphError(f"Supplying file already exists: {path}")
        return file, available, is_deferred, new_relation

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
        new_file_is = [file.i for file, _, _, new_relation in resolved if new_relation]
        if len(new_file_is) > 0:
            node.check_no_cycle_batch(new_file_is)
        return [
            SupplyInfo(
                file,
                available,
                is_deferred,
                new_idep=(node.add_source(file, skip_cycle_check=True) if new_relation else None),
            )
            for file, available, is_deferred, new_relation in resolved
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
            The desired file state if any.
            (None for supplying files, not None in all other cases)

        Returns
        -------
        file
            The key of the created / recycled file.
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
        if not (creator.kind() == "st" or self._matching_static_tree(path) is None):
            raise GraphError("Cannot manually add a file that matches a static tree.")
        self._raise_if_forbidden_target(path, file_state)

        file = self.create(File, creator, path, state=file_state)

        if file_state == FileState.VOLATILE:
            # Do not allow volatile files to have sinks.
            if any(file.sinks()):
                raise GraphError(f"An input to an existing step cannot be volatile: {path}")
        else:
            # Watch parent directories of non-volatile files.
            self.put_dir_queue(Path(path).parent)
        return file

    def reconcile_targets(self):
        """Validate targets against the loaded graph and flag affected steps for recompute.

        Declaration-time validation (in `_declare_file` and `_resolve_supply_file`) only
        runs when `define_step`/`amend_step`/`declare_unconfirmed` are actually called, which
        does not happen for a database-resumed run against an unchanged `plan.py`. Call
        this once at director startup, after the boot/resume step
        (`DirectorHandler.initialize_boot`/`startup_from_db`) so that a changed `plan.py`
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

    def _build_to_check(self, deferred: Collection[Node]) -> list[tuple[str, FileHash]]:
        """Convert a list of UNCONFIRMED file nodes to a list of (path, file_hash) tuples.

        This list is intended to be returned to the caller, so the validity of the files
        can be checked and confirmed as static in a follow-up RPC call.

        Parameters
        ----------
        deferred
            UNCONFIRMED file nodes that match a static tree.

        Returns
        -------
        to_check
            A list of paths and file_hashes.
            These must be sent back to the client where the hashes can be checked
            and which then calls `confirm_hashes` with the updated hashes.
        """
        db = self.db
        db.execute("DELETE FROM node_list")
        db.executemany("INSERT INTO node_list VALUES (?)", ((file.i,) for file in deferred))
        sql = (
            "SELECT node.label, file.hash FROM node_list "
            "JOIN node ON node.i = node_list.i "
            "JOIN file ON file.node = node_list.i "
            "ORDER BY node.label"
        )
        return [(path, FileHash.from_json(hash_value)) for path, hash_value in db.execute(sql)]

    #
    # Build phase (low-level public API)
    #

    def declare_unconfirmed(
        self, creator: Node, paths: Collection[str]
    ) -> list[tuple[str, FileHash]]:
        """Declare files as unconfirmed static candidates, to be confirmed shortly after.

        A file declared here becomes STATIC once confirmed present, or MISSING once
        confirmed absent (see `confirm_hashes`).

        Parameters
        ----------
        creator
            The node creating this file (or None if not known).
        paths
            The locations of the files or directories (ending with /).

        Returns
        -------
        to_check
            A list of paths and file_hashes.
            These must be sent back to the client where the hashes can be checked
            and which then calls `confirm_hashes` with the updated hashes.
        """
        if isinstance(paths, str):
            raise TypeError("The paths argument cannot be a string.")
        if creator.is_detached():
            # The creator has moved on without this call (see Step.detach()), so
            # declaring more files for it is moot.
            return []
        # Sort paths to make the operation deterministic.
        paths = sorted(set(paths))
        # Define the files and create a list of (path, file_hash) tuples.
        unconfirmed = [self._declare_file(creator, path, FileState.UNCONFIRMED) for path in paths]
        # Collect a list of paths and file hashes to be checked.
        return self._build_to_check(unconfirmed)

    def register_static_tree(self, creator: Node, path: str) -> list[tuple[str, FileHash]]:
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
            A list of matching (path, file_hash) whose existence and validity must be checked.
            The client must call `confirm_hashes` after checking files with resulting hashes.
        """
        if not isinstance(path, str):
            raise TypeError("The argument path must be a string.")
        if Path(path).isabs():
            raise ValueError(f"Static tree paths cannot be absolute paths: {path}")
        if has_wildcards(path):
            raise ValueError(f"Static tree does not support wildcards: {path}")
        if creator.is_detached():
            # The creator has moved on without this call (see Step.detach()), so
            # registering a static tree for it is moot.
            return []
        path = Path(path) / ""
        if self._matching_static_tree(path) is not None:
            raise GraphError(f"Static tree is a subdirectory of an existing static tree: {path}")
        sql = "SELECT 1 FROM node WHERE kind = 'st' AND NOT detached AND label LIKE ? ESCAPE '\\'"
        pattern = f"{escape_like_pattern(path)}%"
        if self.db.execute(sql, (pattern,)).fetchone() is not None:
            raise GraphError(
                f"Static tree is a parent directory of an existing static tree: {path}"
            )
        st = self.create(StaticTree, creator, path)
        # Check for matches in existing files.
        # For example previously defined inputs whose origin was not determined yet.
        # UNCONFIRMED and MISSING files are excluded: both are already attached to whatever
        # creator declared them, so matching them here would make declare_unconfirmed try to
        # reattach an already-attached node and raise a GraphError.
        pattern = f"{escape_like_pattern(path)}%"
        sql = (
            "SELECT label FROM node JOIN file ON node.i = file.node "
            f"WHERE state NOT IN ({FileState.UNCONFIRMED.value}, {FileState.MISSING.value}) "
            "AND node.label LIKE ?"
        )
        matching_paths = [path for (path,) in self.db.execute(sql, (pattern,))]
        return self.declare_unconfirmed(st, matching_paths)

    def register_nglob(self, step: Step, nglob_multi: NGlobMulti):
        if not isinstance(step, Step):
            raise TypeError(f"step must be a Step instance, got: {step!r}")
        if step.is_detached():
            # The step's creator has moved on without it (see Step.detach()), so
            # registering more nglobs for it is moot.
            return
        step.register_nglob(nglob_multi)

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
    ) -> list[tuple[File, FileState]]:
        """Define a new step.

        Parameters
        ----------
        creator
            The step that generated this step.
            This is None for the boot script.
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
        safe
            The initial value for the `safe` field of the step.
            This is an internal field, not controlled by the end user.
            It is used to prevent steps from being queued if their creator is not
            RUNNING or SUCCEEDED.
            The only exception is the top-level `plan.py` step, which is always safe to queue.

        Returns
        -------
        to_check
            A list of paths and file_hashes.
            These must be sent back to the client where the hashes can be checked
            and which then calls `confirm_hashes` with the updated hashes.
        """
        if need == Need.TARGET:
            raise GraphError(
                "need=Need.TARGET is reserved for derived elevation; "
                "it cannot be passed to define_step."
            )

        if creator.is_detached():
            # The creator has moved on without this call (see Step.detach()), so
            # defining a new child step for it is moot.
            # (This also sidesteps Node.recycle()'s "new creator must not be detached"
            # check, which Trellis.recycle() below could otherwise hit.)
            return []

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
            inp_paths=inp_paths,
            env_deps=env_deps,
            out_paths=out_paths,
            vol_paths=vol_paths,
        )
        if old_step is not None:
            # Look for UNCONFIRMED inputs that match a static tree. Their existence still
            # needs to be checked by the client and ideally confirmed as existing in a
            # follow-up call to `confirm_hashes`.
            deferred = {
                File(self, i, label) for i, label in self.db.execute(DEFERRED_INPUTS, (old_step.i,))
            }
            return self._build_to_check(deferred)

        # Create new step
        step = self.create(
            Step,
            creator,
            command,
            workdir=workdir,
            need=need,
            safe=safe,
            subshell=subshell,
        )
        step.set_resources(resources)
        step.set_env_overrides(env_overrides)

        # Keep track of all missing files that match a static tree and need to be confirmed.
        deferred = set()

        # Supply inp_paths
        for info in self._supply_files(step, inp_paths):
            # We do not care about the unavailable files here,
            # because the step will only be executed when all inputs are available.
            if info.is_deferred:
                deferred.add(info.file)

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
        return self._build_to_check(deferred)

    def amend_step(
        self,
        step: Step,
        *,
        inp_paths: Collection[str] = (),
        env_deps: Collection[str] = (),
        out_paths: Collection[str] = (),
        vol_paths: Collection[str] = (),
        ran_concurrently: Callable[[int, int], bool],
    ) -> tuple[bool, set[str], set[str], list[tuple[File, FileState]]]:
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
            A list of paths and file_hashes.
            These must be sent back to the client where the hashes can be checked
            and which then calls `confirm_hashes` with the updated hashes.
            (This is only relevant when carry_on is True.
            If some to_check files turn out to be missing, carry_on should be changed to False.)
        """
        if not isinstance(step, Step):
            raise TypeError(f"step must be a Step instance, got: {step!r}")
        if step.is_detached():
            # The step's creator has moved on without it (see Step.detach()), so
            # its amendments are moot.
            return True, set(), set(), []

        # Normalize arguments
        inp_paths = sorted(set(inp_paths))
        out_paths = sorted(set(out_paths))
        vol_paths = sorted(set(vol_paths))
        if any(inp_path.endswith(os.sep) for inp_path in inp_paths):
            raise GraphError("Directory inputs are not supported.")

        # Keep track of missing files, of which there are three different types:
        # - unavailable = certainly not available
        # - unfresh = available, but fails the amend() freshness check.
        # - deferred = possibly available but need to be checked.
        #   For example, these can be UNCONFIRMED files that need to be confirmed as STATIC
        #   (or MISSING).
        unavailable = set()
        unfresh = set()
        deferred = set()
        amended_ideps = []

        # Process inp_paths
        infos = self._supply_files(step, inp_paths, new=False)
        for info in infos:
            if not info.available:
                unavailable.add(info.file.path)
            elif info.file.get_state() == FileState.BUILT:
                producer = info.file.creator()
                if isinstance(producer, Step) and ran_concurrently(producer.i, step.i):
                    unfresh.add(info.file.path)
            if info.new_idep is not None:
                amended_ideps.append((info.new_idep,))
            if info.is_deferred:
                deferred.add(info.file)

        # Process vars
        step.amend_env_deps(env_deps)

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
        return False, unavailable, unfresh, self._build_to_check(deferred)

    #
    # Watch phase
    #

    def is_relevant(self, path: str) -> bool:
        file, detached = self.find_detached(File, path)
        if not (file is None or detached):
            return file.get_state() not in (FileState.AWAITED, FileState.VOLATILE)
        return any(ngm.may_change(set(), {path}) for ngm in self.nglob_multis())

    def relevant_paths(self, parent: str) -> Iterator[str]:
        """Iterate over all non-detached files that are relevant for a given parent directory."""
        sql = (
            "SELECT label FROM node JOIN file ON node.i = file.node "
            f"WHERE state NOT IN ({FileState.AWAITED.value}, {FileState.VOLATILE.value}) AND "
            "node.label LIKE ? AND NOT detached"
        )
        pattern = f"{escape_like_pattern(parent)}%"
        for (path,) in self.db.execute(sql, (pattern,)):
            yield path

    def nglob_multis(self, yield_step: bool = False) -> Iterator[NGlobMulti]:
        sql = (
            "SELECT node.i, label, kind, nglob_multi.i, data "
            "FROM node JOIN nglob_multi ON node.i = nglob_multi.node"
        )
        for node_i, label, kind, ngm_i, data in self.db.execute(sql):
            if kind != "step":
                raise ValueError("Only steps can define nglob_multis")
            nglob_multi = pickle.loads(data)
            yield (ngm_i, nglob_multi, Step(self, node_i, label)) if yield_step else nglob_multi

    def process_nglob_changes(self, deleted: Collection[str], added: Collection[str]):
        """Mark steps with nglob pending if they are affected by the deleted and updated paths.

        Parameters
        ----------
        deleted
            The deleted files.
        added
            The added.
        """
        if deleted & added:
            raise ValueError("Deleted and added paths cannot overlap.")
        for i, ngm, step in self.nglob_multis(yield_step=True):
            # Check if any of the deleted files matches an nglob.
            # If yes, step becomes pending.
            # Check if added files could result in new nglob matches.
            # If yes, step becomes pending.
            evolved = ngm.will_change(deleted, added)
            if evolved is not None:
                step.delete_hash()
                data = (pickle.dumps(evolved), i)
                self.db.execute("UPDATE nglob_multi SET data = ? WHERE i = ?", data)
                self.mark_step_pending(step)

    def get_file_hashes(self, paths: Collection[str]) -> list[tuple[str, FileHash]]:
        """Get the hashes of existing files.

        Parameters
        ----------
        paths
            A list of paths.

        Returns
        -------
        file_hashes
            A list of `(path, file_hash)` tuples.
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
        return [(path, FileHash.from_json(hash_value)) for path, hash_value in db.execute(sql)]

    def put_dir_queue(self, path: str):
        """Put a directory in the dir_queue, with some consistency checks."""
        path = Path(path)
        if path == "":
            path = Path(".")
        if self.makedirs:
            path.makedirs_p()
        if self.dir_queue is not None:
            self.dir_queue.put_nowait(path)
