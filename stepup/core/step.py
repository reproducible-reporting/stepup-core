# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""A `Step` is a command that can be executed and that has inputs and/or outputs."""

import json
import logging
import os
import pickle
from collections.abc import Iterator
from typing import Self

import attrs
from path import Path

from .enums import FileState, Need, StepState
from .file import File
from .hash import FileHash, StepHash
from .nglob import NGlobMulti
from .static_tree import StaticTree
from .stepinfo import StepInfo
from .trellis import Node, NodeType
from .utils import format_digest

__all__ = ("RESERVED_ENV_VARS", "Step", "truncate_output")


logger = logging.getLogger(__name__)


# Environment variables that StepUp sets for each step (see Executor.run).
# These are managed by StepUp and must not be amended as env dependencies or set as overrides.
RESERVED_ENV_VARS = frozenset(
    {"HERE", "ROOT", "STEPUP_STEP_I", "STEPUP_STEP_INP_DIGEST", "STEPUP_STEP_NEED"}
)


STEP_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS step (
    -- Main data
    node INTEGER PRIMARY KEY,
    -- The node of the step in the node table.
    state INTEGER NOT NULL CHECK(state >= 21 AND state <= 25),
    -- The state of the step, as defined in the StepState enum.
    need INTEGER NOT NULL CHECK(need >= 31 AND need <= 34),
    -- The need of the step, as defined in the Need enum.
    duration REAL NOT NULL CHECK(duration >= 0) DEFAULT 1.0,
    -- An estimate of the wall time of the step in seconds.
    postponed INTEGER NOT NULL CHECK(postponed IN (0, 1)) DEFAULT 0,
    -- Whether the step is postponed due to missing inputs (see StepState.PENDING).
    postpone_count INTEGER NOT NULL CHECK(postpone_count >= 0) DEFAULT 0,
    -- Number of consecutive postpones since the last SUCCEEDED state.
    -- Reset to 0 only on SUCCEEDED (see step_reset_postpone_count below);
    -- NOT reset by FAILED or by postponed being cleared via mark_pending().
    subshell INTEGER NOT NULL CHECK(subshell IN (0, 1)),
    -- Whether the step command is executed via a subshell (shell=True).
    env_overrides TEXT,
    -- JSON-encoded dict[str, str] of step-specific environment variable overrides, or NULL.
    -- Applied to the child process environment when the step runs.
    -- Metadata
    _safe INTEGER NOT NULL CHECK(_safe IN (0, 1)),
    -- Whether this step is safe to run, meaning that all its (recursive) creators
    -- are in a state that allows queuing this step (RUNNING or SUCCEEDED).
    _check_safe INTEGER NOT NULL CHECK(_check_safe IN (0, 1)),
    -- Whether recent changes to this step imply updates of the _safe metadata field of others.
    _implied_need INTEGER NOT NULL CHECK(_implied_need >= 31 AND _implied_need <= 34),
    -- The need that is implied by sinks, as defined in the Need enum.
    _tail_time REAL NOT NULL CHECK(_tail_time >= 0) DEFAULT 1.0,
    -- The tail_time of this step, defined as the total duration of the critical path from this step
    -- to the exit nodes of the workflow.
    _check_after INTEGER NOT NULL CHECK(_check_after IN (0, 1)),
    -- Whether recent changes to this step require the recalculation of the _implied_need
    -- metadata of this step and its sources.

    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE
) WITHOUT ROWID;

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS step_state ON step(state);
CREATE INDEX IF NOT EXISTS step_implied_need ON step(_implied_need);
-- Partial indexes over the scheduler's "to recompute" flags. They contain only the few
-- flagged rows, so locating them each scheduling tick (see Scheduler._update_meta_*) is an
-- index scan proportional to the flagged count instead of a full scan of the step table.
CREATE INDEX IF NOT EXISTS step_check_safe ON step(node) WHERE _check_safe;
CREATE INDEX IF NOT EXISTS step_check_after ON step(node) WHERE _check_after;
-- Partial index matching Scheduler._PENDING_STEP_WHERE's static predicates, so
-- SELECT_CHECKABLE_STEPS/SELECT_RUNNABLE_STEPS can jump to plausible dispatch
-- candidates instead of scanning every PENDING step to test _safe/postponed.
CREATE INDEX IF NOT EXISTS step_pending_ready ON step(state, _implied_need)
    WHERE _safe AND NOT postponed;

-- Convention for this trigger block: single-row, same-table consequences of a column
-- write live here as triggers. Multi-row / recursive graph consequences (e.g. flagging
-- a step's recursive products, or steps reached across dependency edges two hops away)
-- stay in explicit Python-invoked SQL instead: see RECURSIVE_CHECK_WITH_PRODUCTS and
-- RECURSIVE_CHECK_AFTER_SOURCES below (used by Step.detach()/Step.recycle()), which
-- together with the triggers here account for the complete _check_safe/_check_after
-- bookkeeping story.

-- Keep _check_after in sync with dependency-edge changes touching either endpoint.
-- A no-op UPDATE (zero rows matched) is harmless when the other endpoint is not a step.
CREATE TRIGGER IF NOT EXISTS step_dependency_check_after_ins AFTER INSERT ON dependency
BEGIN
    UPDATE step SET _check_after = 1 WHERE node IN (NEW.source, NEW.sink);
END;
CREATE TRIGGER IF NOT EXISTS step_dependency_check_after_del AFTER DELETE ON dependency
BEGIN
    UPDATE step SET _check_after = 1 WHERE node IN (OLD.source, OLD.sink);
END;

-- Keep _check_after in sync with duration changes, so the scheduler recomputes
-- _implied_need/_tail_time for this step (and, via propagation, its sources).
CREATE TRIGGER IF NOT EXISTS step_flag_check_after_duration AFTER UPDATE OF duration ON step
BEGIN
    UPDATE step SET _check_after = 1 WHERE node = NEW.node;
END;

-- Keep _check_safe in sync with state changes, so the scheduler recomputes the _safe
-- metadata of this step (and, via propagation, its products).
CREATE TRIGGER IF NOT EXISTS step_flag_check_safe AFTER UPDATE OF state ON step
BEGIN
    UPDATE step SET _check_safe = 1 WHERE node = NEW.node;
END;

-- Clear postponed once a step reaches a completed state, so a
-- stale postpone note from a previous run does not keep gating schedulability after
-- it settles again.
CREATE TRIGGER IF NOT EXISTS step_clear_postponed AFTER UPDATE OF state ON step
WHEN NEW.state IN ({StepState.SUCCEEDED.value}, {StepState.FAILED.value})
BEGIN
    UPDATE step SET postponed = FALSE WHERE node = NEW.node;
END;

-- Reset postpone_count only on SUCCEEDED (not FAILED), so the cap measures
-- consecutive postpone attempts since the last convergence, independent of
-- (broader) postponed SUCCEEDED-or-FAILED clearing above.
CREATE TRIGGER IF NOT EXISTS step_reset_postpone_count AFTER UPDATE OF state ON step
WHEN NEW.state = {StepState.SUCCEEDED.value}
BEGIN
    UPDATE step SET postpone_count = 0 WHERE node = NEW.node;
END;

-- Satellite tables below hold auxiliary per-step data. All are keyed by (or include) the
-- step's node and are removed via ON DELETE CASCADE when the node row is deleted.

-- Named glob patterns (with back-references) registered by this step; see NGlobMulti.
CREATE TABLE IF NOT EXISTS nglob_multi (
    i INTEGER PRIMARY KEY,
    node INTEGER NOT NULL,
    data BLOB NOT NULL,
    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS nglob_multi_node ON nglob_multi(node);

-- Marks which dependency rows were amended (discovered while the step ran) rather than
-- declared up front, so reset_for_rerun() knows which sources/sinks to drop between runs.
CREATE TABLE IF NOT EXISTS amended_dep (
    i INTEGER PRIMARY KEY,
    FOREIGN KEY (i) REFERENCES dependency(i) ON DELETE CASCADE
) WITHOUT ROWID;

-- Environment variable names this step depends on, and the value observed when recorded
-- (declared up front or amended during the run).
CREATE TABLE IF NOT EXISTS env_var (
    node INTEGER NOT NULL,
    name TEXT NOT NULL,
    value TEXT,
    amended INTEGER NOT NULL CHECK(amended IN (0, 1)),
    PRIMARY KEY (node, name)
    -- The PRIMARY KEY above already indexes lookups by node (leftmost column),
    -- so no separate index on (node) is needed.
    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE
) WITHOUT ROWID;

-- The stored hash of the step's last successful run, used to decide whether a rerun can be
-- skipped.
CREATE TABLE IF NOT EXISTS step_hash (
    node INTEGER PRIMARY KEY,
    hash TEXT NOT NULL,
    -- JSON-encoded StepHash of the last successful run.
    -- Absence of a row means no hash is stored (e.g. never run, or reset via Step.delete_hash).
    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE,
    CHECK (json_valid(hash))
);

-- Captured standard output/error of the step's command, for the "show output" feature.
CREATE TABLE IF NOT EXISTS step_output (
    node INTEGER PRIMARY KEY,
    stdout TEXT NOT NULL DEFAULT '',
    stderr TEXT NOT NULL DEFAULT '',
    -- Captured standard output/error of the step's command.
    -- Absence of a row means no output has been recorded for this run.
    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE
);

-- Named resource units (e.g. a semaphore-like GPU or license count) claimed by this step
-- while it runs; consumed by the scheduler to cap concurrent resource usage.
CREATE TABLE IF NOT EXISTS step_resource (
    node  INTEGER NOT NULL,
    name  TEXT    NOT NULL CHECK(name <> ''),
    units INTEGER NOT NULL CHECK(units > 0),
    PRIMARY KEY (node, name),
    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS step_resource_name ON step_resource(name);

-- Subprocess invocations made by this (wrapper) step, recorded for archival/debugging via
-- Step.record_subprocess; informative only, not authoritative.
CREATE TABLE IF NOT EXISTS step_subprocess (
    node INTEGER NOT NULL,
    -- The step node that ran this subprocess.
    cmd TEXT NOT NULL,
    -- The command line, as a single shell-quoted string.
    workdir TEXT NOT NULL DEFAULT './',
    -- The working directory of the subprocess, relative to STEPUP_ROOT.
    env_overrides TEXT,
    -- JSON-encoded dict[str, str] overlay of environment variables set on top of the
    -- inherited environment, or NULL when no overlay was applied.
    returncode INTEGER NOT NULL,
    -- The exit code of the subprocess.
    shell INTEGER NOT NULL DEFAULT 0,
    -- Whether cmd was executed via a shell (1) or directly (0).
    stdin TEXT,
    -- The standard input fed to the subprocess, or NULL.
    stdout TEXT,
    -- The captured standard output of the subprocess, or NULL if not captured.
    stderr TEXT,
    -- The captured standard error of the subprocess, or NULL if not captured.
    -- ON DELETE CASCADE removes these rows when the node row is deleted, matching the
    -- other satellite tables (env_var / step_hash / step_output / step_resource).
    -- Step.reset_for_rerun() still clears them explicitly between runs of a surviving step.
    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS step_subprocess_node ON step_subprocess(node);
"""


def truncate_output(content: str, max_size: int) -> str:
    """Truncate `content` to at most `max_size` UTF-8 bytes, appending a sentinel if cut.

    A `max_size` of `0` (or any non-positive value) means unlimited: the content is
    returned unchanged. Otherwise the cut is made on a valid UTF-8 character boundary
    (`decode(..., "ignore")` drops a trailing partial multi-byte sequence), so the result
    is always valid text.

    Parameters
    ----------
    content
        The captured text to (possibly) truncate.
    max_size
        Maximum number of UTF-8 bytes to keep, or `0` (or any non-positive value) for
        unlimited.

    Returns
    -------
    truncated
        The original content if within the budget or unlimited, otherwise the content cut
        to `max_size` bytes with a sentinel line appended.
    """
    if max_size <= 0:
        return content
    encoded = content.encode("utf-8")
    if len(encoded) <= max_size:
        return content
    truncated = encoded[:max_size].decode("utf-8", "ignore")
    return f"{truncated}\n[output truncated at {max_size} bytes]\n"


# When a step is detached or recycled, its creator chain changes, which alters the "safe" state
# of the step and of every step it created (recursively): whether their (indirect) creator is in
# a state that allows queuing them. Flag _check_safe (and _check_after) on the step and all its
# product steps so the scheduler recomputes their metadata.
RECURSIVE_CHECK_WITH_PRODUCTS = """
UPDATE step SET _check_safe = 1, _check_after = 1 FROM (
    WITH RECURSIVE check_with_products(node) AS (
        -- Fairly trivial initialization, will only work if the node is a step.
        SELECT node FROM step WHERE node = ?
        UNION ALL
        -- Recurse over all product steps of the step.
        -- Products that are not steps can be ignored.
        SELECT i FROM node
        JOIN check_with_products ON node.creator = check_with_products.node
        WHERE node.kind = 'step'
    )
    SELECT node FROM check_with_products
) AS cwp WHERE step.node = cwp.node
"""


# When a step (sub)tree is detached, the steps supplying inputs to it lose a sink.
# Their _implied_need and _tail_time must therefore be recomputed, so flag _check_after on them.
# Sources are reached with two dependency hops: subtree_step <- input_file <- source_step.
# Only non-detached source steps are flagged; detached ones are excluded from the metadata anyway.
RECURSIVE_CHECK_AFTER_SOURCES = """
UPDATE step SET _check_after = 1 FROM (
    WITH RECURSIVE subtree(node) AS (
        -- Start from the detached step and recurse over its product steps (the detached subtree).
        SELECT node FROM step WHERE node = ?
        UNION ALL
        SELECT i FROM node
        JOIN subtree ON node.creator = subtree.node
        WHERE node.kind = 'step'
    )
    -- Two hops back along dependency edges to the source steps of the subtree.
    SELECT DISTINCT dep2.source AS node
    FROM subtree
    JOIN dependency AS dep1 ON dep1.sink = subtree.node
    JOIN dependency AS dep2 ON dep2.sink = dep1.source
    JOIN node AS source_node ON source_node.i = dep2.source
    WHERE source_node.kind = 'step' AND NOT source_node.detached
) AS sup WHERE step.node = sup.node
"""


@attrs.define(frozen=True)
class PathRecord:
    """One path yielded by `Step._paths()` and its wrapper methods."""

    path: str
    """The path, relative to the workflow root or the step's work directory."""

    state: FileState
    """The state of the file."""

    detached: bool
    """Whether the path is detached, i.e. its creating step moved on without it."""

    amended: bool
    """Whether the path was amended, i.e. discovered while the step was running."""

    _hash_json: str | None = attrs.field(repr=False)
    """The JSON representation of the file's hash, decoded lazily through `hash`."""

    @property
    def hash(self) -> FileHash:
        """The file's hash, lazily decoded from its JSON representation."""
        return FileHash.from_json(self._hash_json)


@attrs.define
class Step(Node):
    #
    # Override from base class
    #

    @classmethod
    def schema(cls) -> str | None:
        """Return node-specific SQL commands to initialize the database."""
        return STEP_SCHEMA

    @classmethod
    def create_label(cls, label: str, workdir: str = ".", **kwargs):
        """Derive the step label from the command and optional working directory."""
        if "  # wd=" in label:
            raise ValueError(
                "Do not include a workdir comment in the command string. "
                "Pass the workdir separately."
            )
        if workdir != ".":
            label += f"  # wd={workdir}"
        return label

    def initialize(
        self,
        *,
        safe: bool = False,
        need: Need = Need.DEFAULT,
        subshell: bool = False,
        **kwargs,  # workdir is consumed by create_label, not used here
    ):
        """Create extra information in the database about this node.

        If a step with this node already exists (i.e. a detached step is being
        recycled), its `step_hash`/`step_output` satellite rows are untouched by this
        `INSERT OR REPLACE`, so a recycled step's stored hash remains available for
        skip-checking after redeclaration instead of being discarded.
        """
        self.db.execute(
            "INSERT OR REPLACE INTO step "
            "(node, state, need, subshell, _safe, _check_safe, _implied_need, _check_after) "
            "VALUES(:node, :state, :need, :subshell, :safe, :check_safe, "
            ":implied_need, :check_need)",
            {
                "node": self.i,
                "need": need.value,
                "state": StepState.PENDING.value,
                "subshell": int(subshell),
                "safe": int(safe),
                "check_safe": int(not safe),
                "implied_need": need.value,
                "check_need": int(need != Need.OPTIONAL),
            },
        )

    def validate(self):
        """Validate extra information about this node is present in the database."""
        row = self.db.execute("SELECT 1 FROM step WHERE node = ?", (self.i,)).fetchone()
        if row is None:
            raise ValueError(f"Step node {self.key()} has no row in the file table.")

    def format_properties(self) -> Iterator[tuple[str, str]]:
        """Iterate over key-value pairs that represent the properties of the node."""
        sql = "SELECT state, need, _implied_need FROM step WHERE node = ?"
        state_id, need_id, implied_need_id = self.db.execute(sql, (self.i,)).fetchone()
        state = StepState(state_id)
        yield "state", state.name
        need = Need(need_id)
        implied_need = Need(implied_need_id)
        if need == implied_need:
            yield "need", need.name
        else:
            yield "need", f"{implied_need.name} (implied by sinks > {need.name})"

        sql = "SELECT name, amended FROM env_var WHERE node = ?"
        label = "using_env"
        for env_var, amended in self.db.execute(sql, (self.i,)):
            yield label, f"{env_var} [amended]" if amended else env_var
            label = ""

        label = "env_overrides"
        for name, value in sorted(self.get_env_overrides().items()):
            yield label, f"{name}={value}"
            label = ""

        for row in self.db.execute("SELECT data FROM nglob_multi WHERE node = ?", (self.i,)):
            ngm = pickle.loads(row[0])
            yield "ngm", f"{[ngs.pattern for ngs in ngm.nglob_singles]} {ngm.subs}"

        for row in self.db.execute(
            "SELECT name, units FROM step_resource WHERE node = ?", (self.i,)
        ):
            yield "resource", f"{row[0]}: {row[1]} units"

        step_hash = self.get_hash()
        if step_hash is not None:
            yield "inp_digest", format_digest(step_hash.inp_digest)
            yield "out_digest", format_digest(step_hash.out_digest)
            if step_hash.inp_info is not None:
                yield "explained", "yes"

    def clean(self):
        """Perform a cleanup right before the detached node is removed from the graph.

        The satellite rows (step, env_var, nglob_multi, step_hash, step_output,
        step_resource, step_subprocess) are removed automatically by `ON DELETE CASCADE`
        when the node row is deleted, so only the dependency edges are handled here.
        """
        self.del_sources()
        for sink in self.sinks(include_detached=True):
            sink.del_sources([self])

    def give_up(self):
        """Clean up a detached node because it loses a product node.

        Completely remove this step, making reuse impossible.
        """
        for sink in self.sinks(include_detached=True):
            sink.del_sources([self])
        for product in self.products():
            product.detach()
        self.detach()
        self.clean()
        self.db.execute("DELETE FROM node WHERE i = ?", (self.i,))

    #
    # Getters and setters
    #

    def _dependencies_str(
        self,
        node_type: type[NodeType] = Self,
        do_sources: bool = True,
    ) -> Iterator[str]:
        sql = (
            "SELECT kind, label, detached, dependency.i IN amended_dep "
            "FROM node JOIN dependency ON node.i = "
        )
        sql += " source WHERE sink = ?" if do_sources else " sink WHERE source = ?"
        data = [self.i]
        if node_type is not Self:
            sql += " AND kind = ?"
            data.append(node_type.kind())
        sql += " ORDER BY kind, label"
        for kind, label, detached, amended in self.db.execute(sql, data):
            node_str = f"{kind}:{label}"
            if detached:
                node_str = f"({node_str})"
            if amended:
                node_str += " [amended]"
            yield node_str

    @property
    def command_workdir(self) -> tuple[str, Path]:
        """The command and working directory of this step."""
        parts = self.label.split("  # wd=", maxsplit=1)
        return parts[0], Path(parts[1] if len(parts) == 2 else ".")

    def get_subshell(self) -> bool:
        """Return whether this step runs the command via a subshell."""
        row = self.db.execute("SELECT subshell FROM step WHERE node = ?", (self.i,)).fetchone()
        return bool(row[0])

    def get_env_overrides(self) -> dict[str, str]:
        """Return the step-specific environment variable overrides."""
        row = self.db.execute("SELECT env_overrides FROM step WHERE node = ?", (self.i,)).fetchone()
        return {} if row[0] is None else json.loads(row[0])

    def set_env_overrides(self, env_overrides: dict[str, str] | None):
        """Set the step-specific environment variable overrides."""
        value = None if not env_overrides else json.dumps(env_overrides)
        self.db.execute("UPDATE step SET env_overrides = ? WHERE node = ?", (value, self.i))

    def get_need(self) -> Need:
        """Return the declared need of this step."""
        row = self.db.execute("SELECT need FROM step WHERE node = ?", (self.i,)).fetchone()
        return Need(row[0])

    def get_state(self) -> StepState:
        row = self.db.execute("SELECT state FROM step WHERE node = ?", (self.i,)).fetchone()
        return StepState(row[0])

    def set_state(self, state: StepState, postponed: bool = False):
        if postponed and not state == StepState.PENDING:
            raise ValueError("postponed can only be True when setting state to PENDING")
        self.db.execute(
            "UPDATE step SET state = ?, postponed = ? WHERE node = ?",
            (state.value, postponed, self.i),
        )

    def has_unavailable_amended_input(self) -> bool:
        """Determine if any amended input dependency is not currently `STATIC` or `BUILT`."""
        sql = f"""
        SELECT EXISTS (
            SELECT 1 FROM dependency
            JOIN amended_dep ON amended_dep.i = dependency.i
            JOIN file ON file.node = dependency.source
            WHERE dependency.sink = ?
            AND file.state NOT IN ({FileState.STATIC.value}, {FileState.BUILT.value})
        )
        """
        return bool(self.db.execute(sql, (self.i,)).fetchone()[0])

    def get_postpone_count(self) -> int:
        """Return the number of consecutive postpones since the last SUCCEEDED state."""
        sql = "SELECT postpone_count FROM step WHERE node = ?"
        return self.db.execute(sql, (self.i,)).fetchone()[0]

    def _increment_postpone_count(self) -> int:
        """Increment postpone_count and return the new value."""
        self.db.execute(
            "UPDATE step SET postpone_count = postpone_count + 1 WHERE node = ?", (self.i,)
        )
        return self.get_postpone_count()

    def set_duration(self, duration: float):
        self.db.execute("UPDATE step SET duration = ? WHERE node = ?", (duration, self.i))

    #
    # Get step information
    #

    def get_step_info(self) -> StepInfo:
        """Return a `StepInfo` object with information about this step.

        Amended information is not included for consistency with
        the information that is available when defining a step.
        """
        if self.is_detached():
            # This step's creator has moved on without it (see Step.detach()); its real
            # info is moot.
            return StepInfo("", [], [], [], [], Path("."))
        command, workdir = self.command_workdir
        return StepInfo(
            command,
            [r.path for r in self.inp_paths(amended=False)],
            self.env_deps(amended=False),
            [r.path for r in self.out_paths(amended=False)],
            [r.path for r in self.vol_paths(amended=False)],
            workdir,
        )

    #
    # Env vars
    #

    def add_env_deps(self, env_deps):
        rows = [(self.i, name, os.getenv(name)) for name in env_deps]
        self.db.executemany("INSERT OR REPLACE INTO env_var VALUES (?, ?, ?, 0)", rows)

    def amend_env_deps(self, env_deps):
        # Ignore variables that this step overrides via env_overrides: their value is fixed by the
        # step, so they are not external dependencies that can change between runs.
        env_overrides = self.get_env_overrides()
        rows = [(self.i, name, os.getenv(name)) for name in env_deps if name not in env_overrides]
        self.db.executemany("INSERT OR IGNORE INTO env_var VALUES (?, ?, ?, 1)", rows)

    #
    # Iterators
    #

    def _paths(
        self,
        relation: str,
        *,
        include_detached: bool = False,
        amended: bool | None = None,
        filter_states: tuple[FileState, ...] = (),
    ) -> Iterator[PathRecord]:
        """Iterate over paths of this step using various criteria."""
        # Which relation?
        data = {"node": self.i}
        if relation == "product":
            # There is no dependency row for a product, so `idep` is NULL, which makes the
            # amended-exists check below (and an `amended=True` filter) naturally resolve to
            # "never amended" -- correct, since a declared static/missing path can't be amended.
            sql = (
                "WITH relevant AS (SELECT i AS node, NULL AS idep FROM node WHERE creator = :node)"
            )
        elif relation == "source":
            sql = (
                "WITH relevant AS "
                "(SELECT source AS node, i AS idep FROM dependency WHERE sink = :node)"
            )
        elif relation == "sink":
            sql = (
                "WITH relevant AS "
                "(SELECT sink AS node, i AS idep FROM dependency WHERE source = :node)"
            )
        else:
            raise ValueError(f"Unrecognized relation argument: '{relation}'")
        join = "JOIN node ON node.i = relevant.node JOIN file ON file.node = relevant.node"
        fields = [
            "label",
            "state",
            "hash",
            "detached",
            "EXISTS (SELECT 1 FROM amended_dep WHERE amended_dep.i = relevant.idep)",
        ]
        where = "WHERE kind = 'file'"

        # Exclude detached paths unless requested
        if not include_detached:
            where += " AND NOT detached"

        # Select only the initial files (not amended)
        if amended is not None:
            if amended:
                join += " JOIN amended_dep ON amended_dep.i = relevant.idep"
            else:
                where += (
                    " AND NOT EXISTS (SELECT 1 FROM amended_dep"
                    " WHERE amended_dep.i = relevant.idep)"
                )

        # Filter certain states
        if len(filter_states) > 0:
            where_states = []
            for i, state in enumerate(filter_states):
                where_states.append(f"state = :state_{i}")
                data[f"state_{i}"] = state.value
            where += f" AND ({' OR '.join(where_states)})"

        sql += f" SELECT {', '.join(fields)} FROM relevant {join} {where}"
        for label, state, hash_json, detached, amended_flag in self.db.execute(sql, data):
            yield PathRecord(label, FileState(state), bool(detached), bool(amended_flag), hash_json)

    def inp_paths(
        self, *, include_detached: bool = False, amended: bool | None = None
    ) -> Iterator[PathRecord]:
        """Iterate over input files of this step."""
        yield from self._paths("source", include_detached=include_detached, amended=amended)

    def out_paths(
        self, *, include_detached: bool = False, amended: bool | None = None
    ) -> Iterator[PathRecord]:
        """Iterate over output files of this step."""
        yield from self._paths(
            "sink",
            include_detached=include_detached,
            amended=amended,
            filter_states=(FileState.AWAITED, FileState.BUILT, FileState.OUTDATED),
        )

    def vol_paths(
        self, *, include_detached: bool = False, amended: bool | None = None
    ) -> Iterator[PathRecord]:
        """Iterate over volatile output files of this step."""
        yield from self._paths(
            "sink",
            include_detached=include_detached,
            amended=amended,
            filter_states=(FileState.VOLATILE,),
        )

    def static_paths(self) -> Iterator[PathRecord]:
        """Iterate over static paths created by this step."""
        yield from self._paths("product", filter_states=(FileState.STATIC,))

    def missing_paths(self) -> Iterator[PathRecord]:
        """Iterate over missing paths created by this step."""
        yield from self._paths("product", filter_states=(FileState.MISSING,))

    def env_deps(self, *, amended: bool | None = None):
        """Iterate over used environment variable names (not values)."""
        sql = "SELECT name FROM env_var WHERE node = ?"
        if amended is not None:
            sql += " AND"
            if not amended:
                sql += " NOT"
            sql += " amended = 1"
        for row in self.db.execute(sql, (self.i,)):
            yield row[0]

    def nglob_multis(self) -> Iterator[NGlobMulti]:
        """Iterate of nglob_multis used by this step."""
        for row in self.db.execute("SELECT data FROM nglob_multi WHERE node = ?", (self.i,)):
            yield pickle.loads(row[0])

    #
    # Build phase
    #

    def reset_for_rerun(self):
        """Reset a step back to its freshly defined state, ready to run again.

        This method discards everything that was produced dynamically by the step's
        previous run (if any), so that a future (re)run starts from a clean slate.
        It is called both right before actually re-executing a step, and whenever
        a step is postponed and won't run again immediately.

        The following are reset:

        - amended inputs and (volatile outputs)
        - amended environment variables

        The following are detached:

        - nglob_multis
        - created steps
        - static file definitions
        - static trees

        The following are marked as outdated:

        - output files that are in state BUILT
        """
        # Drop amended sources.
        rows = list(
            self.db.execute(
                "SELECT dependency.i, node.i, node.label, node.kind FROM dependency "
                "JOIN node ON node.i = source "
                "JOIN amended_dep ON amended_dep.i = dependency.i WHERE sink = ?",
                (self.i,),
            )
        )
        self.db.executemany("DELETE FROM amended_dep WHERE i = ?", ((row[0],) for row in rows))
        self.del_sources(
            [self.graph.node_classes[kind](self.graph, i, label) for _, i, label, kind in rows]
        )

        # Drop amended environment variables
        self.db.execute("DELETE FROM env_var WHERE node = ? AND amended = 1", (self.i,))

        # Drop nglob_multis
        self.db.execute("DELETE FROM nglob_multi WHERE node = ?", (self.i,))

        # Drop amended sinks and detach the corresponding sink nodes.
        records_sink = list(
            self.db.execute(
                "SELECT dependency.i, sink, label, kind FROM dependency "
                "JOIN amended_dep ON amended_dep.i = dependency.i "
                "JOIN node ON sink = node.i "
                "WHERE source = ?",
                (self.i,),
            )
        )
        ideps_sink = [(row[0],) for row in records_sink]
        self.db.executemany("DELETE FROM amended_dep WHERE i = ?", ideps_sink)
        for _, i, label, kind in records_sink:
            node = self.graph.node_classes[kind](self.graph, i, label)
            node.del_sources([self])
            node.detach()

        self._detach_created_steps()

        # Detach static file definitions
        sql = (
            "SELECT i, label FROM node JOIN file ON node.i = file.node "
            "WHERE creator = ? AND state in (?, ?)"
        )
        data = (self.i, FileState.STATIC.value, FileState.MISSING.value)
        for i, label in self.db.execute(sql, data):
            file = File(self.graph, i, label)
            file.detach()

        # Detach static trees
        sql = "SELECT i, label FROM node WHERE creator = ? AND kind = 'st'"
        for i, label in self.db.execute(sql, (self.i,)):
            st = StaticTree(self.graph, i, label)
            st.detach()

        # Mark BUILT outputs OUTDATED.
        sql = (
            "SELECT i, label FROM node JOIN file ON node.i = file.node "
            "WHERE creator = ? AND state = ?"
        )
        data = (self.i, FileState.BUILT.value)
        for i, label in self.db.execute(sql, data):
            file = File(self.graph, i, label)
            self.graph.mark_file_outdated(file)

        # Drop any output stored by a previous run.
        self.delete_outputs()

        # Drop any subprocess invocations recorded by a previous run.
        self.delete_subprocesses()

    def _detach_created_steps(self):
        """Detach steps created by this step (e.g. via `run()`/`step()`).

        Called unconditionally by `reset_for_rerun()`, and by `completed()` only when a
        step reaches a genuine terminal `FAILED` state (not on an accepted postpone):
        the discarded run's children must not keep running (or linger attached) even
        before the creator's actual rerun happens, which may be much later. Unlike
        `reset_for_rerun()`, this does not touch amended dependencies, so a postpone
        triggered by an unavailable amended input does not sever the dependency edge
        that `mark_pending()` relies on to wake the step up again once that input
        becomes available.

        A still-`RUNNING` detached step is not killed: it keeps running until its
        command terminates on its own, at which point `completed()`'s `is_detached()`
        branch discovers this and reports it as `DETACHED` (see `Executor.report()`).
        If such a command never terminates, the build hangs; this is a deliberate
        trade-off, not an oversight.
        """
        sql = "SELECT i, label FROM node WHERE creator = ? AND kind = 'step'"
        for i, label in self.db.execute(sql, (self.i,)):
            step = Step(self.graph, i, label)
            step.detach()

    def completed(self, new_hash: StepHash | None, wants_postpone: bool) -> tuple[bool, bool]:
        """Set a step as completed (succeeded or failed) and trigger the consequences.

        Parameters
        ----------
        new_hash
            The new digest of the completed step if the step was successful, `None` otherwise.
        wants_postpone
            True if the step wants to be postponed, False otherwise.

        Returns
        -------
        detached
            True if the step had already been detached by its creator (see `Step.detach()`)
            before this call, in which case the outcome below was not applied: the step is
            superseded, not failed or succeeded.
        interrupted_postpone
            True if postponement has been interrupted due to cap being exceeded, False otherwise.
        """
        if self.is_detached():
            # This step's creator has moved on without it. It is superseded, not failed:
            # mark it PENDING rather than FAILED so it does not taint the build's outcome.
            # A detached PENDING step is silently ignored everywhere else (scheduling,
            # reporting, ...); the executor still reports the fact that it was detached.
            self.set_state(StepState.PENDING)
            return True, False

        interrupted_postpone = False
        if new_hash is None:
            # Update states, needed for files that have not changed since previous run.
            for file in self.products(File):
                if file.get_state() == FileState.BUILT:
                    file.set_state(FileState.OUTDATED)
            if wants_postpone:
                postpone_count = self._increment_postpone_count()
                if postpone_count <= self.graph.postpone_cap:
                    logger.info(
                        "Postponed step (%d/%d): %s",
                        postpone_count,
                        self.graph.postpone_cap,
                        self.label,
                    )
                    # We just set the state to PENDING.
                    # However, it will not be scheduled as long as `postponed` is set to True.
                    # Any later file changes relevant to the step will result in
                    # a call to mark_pending(), which will clear the postponed flag.
                    # This makes the step eligible for scheduling again.
                    postponed = self.has_unavailable_amended_input()
                    self.set_state(StepState.PENDING, postponed)
                else:
                    logger.info(
                        "Postpone cap (%d) exceeded, failed step: %s",
                        self.graph.postpone_cap,
                        self.label,
                    )
                    self.set_state(StepState.FAILED)
                    interrupted_postpone = True
            else:
                logger.info("Failed step: %s", self.label)
                self.set_state(StepState.FAILED)
            if self.get_state() == StepState.FAILED:
                # The step may have created product steps that are already running
                # opportunistically. A genuine terminal failure detaches all of them;
                # an accepted postpone (state stays PENDING) does not, since this step
                # will run again soon and its children should stay attached until then.
                self._detach_created_steps()
            # An unsuccessful step is not skippable, so we're removing its hash.
            self.delete_hash()
        else:
            logger.info("Succeeded step: %s", self.label)
            self.set_state(StepState.SUCCEEDED)
            # Update states, needed for files that have not changed since previous run.
            for file in self.products(File):
                if file.get_state() == FileState.OUTDATED:
                    file.set_state(FileState.BUILT)
                    self.graph.mark_sinks_pending(file)
            self.set_hash(new_hash)
        return False, interrupted_postpone

    def get_hash(self) -> StepHash | None:
        """Return the stored step hash, or `None` if none is stored."""
        row = self.db.execute("SELECT hash FROM step_hash WHERE node = ?", (self.i,)).fetchone()
        return None if row is None else StepHash.from_json(row[0])

    def set_hash(self, step_hash: StepHash):
        """Store the step hash."""
        self.db.execute(
            "INSERT OR REPLACE INTO step_hash VALUES (?, ?)", (self.i, step_hash.to_json())
        )

    def delete_hash(self):
        """Clear the stored step hash, if any."""
        self.db.execute("DELETE FROM step_hash WHERE node = ?", (self.i,))

    def store_output(self, stdout: str, stderr: str, max_size: int) -> None:
        """Persist captured stdout/stderr for this step in a single update.

        Parameters
        ----------
        stdout
            The captured standard output of the step's command (untruncated).
        stderr
            The captured standard error of the step's command (untruncated).
        max_size
            Maximum number of UTF-8 bytes to store per stream, or `0` for unlimited.
            See `truncate_output`.
        """
        self.db.execute(
            "INSERT OR REPLACE INTO step_output VALUES (?, ?, ?)",
            (
                self.i,
                truncate_output(stdout, max_size),
                truncate_output(stderr, max_size),
            ),
        )

    def get_output(self) -> tuple[str, str]:
        """Return the stored (stdout, stderr) for this step, as empty strings if absent."""
        row = self.db.execute(
            "SELECT stdout, stderr FROM step_output WHERE node = ?", (self.i,)
        ).fetchone()
        return ("", "") if row is None else row

    def delete_outputs(self) -> None:
        """Remove the stored stdout/stderr for this step."""
        self.db.execute("DELETE FROM step_output WHERE node = ?", (self.i,))

    def record_subprocess(
        self,
        cmd: str,
        workdir: str,
        env_overrides: dict[str, str] | None,
        returncode: int,
        shell: bool,
        stdin: str,
        stdout: str,
        stderr: str,
    ) -> None:
        """Record a subprocess invocation made by this (wrapper) step.

        The recorded metadata is informative for archival and debugging, not authoritative.
        The `env_overrides` overlay holds only the variables the wrapper explicitly set
        on top of the inherited environment, not the full resolved environment.

        Parameters
        ----------
        cmd
            The command line, as a single shell-quoted string, stored verbatim.
        workdir
            The working directory of the subprocess, relative to `STEPUP_ROOT`.
        env_overrides
            The environment overlay (variables set on top of the inherited environment),
            or `None` when no overlay was applied.
        returncode
            The exit code of the subprocess.
        shell
            Whether `cmd` was executed via a shell (`subprocess.run(..., shell=True)`).
        stdin
            The standard input fed to the subprocess as a string.
        stdout, stderr
            The captured standard output/error of the subprocess as a string.
        """
        if self.is_detached():
            # This step's creator has moved on without it (see Step.detach()); recording
            # is moot.
            return
        # Invocation order is preserved by the table's rowid insertion order, so no
        # separate sequence number needs to be looked up or assigned here.
        self.db.execute(
            "INSERT INTO step_subprocess "
            "(node, cmd, workdir, env_overrides, returncode, shell, stdin, stdout, stderr) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.i,
                cmd,
                workdir,
                None if env_overrides is None else json.dumps(env_overrides),
                returncode,
                int(shell),
                stdin,
                stdout,
                stderr,
            ),
        )

    def delete_subprocesses(self) -> None:
        """Remove all recorded subprocess rows for this step."""
        self.db.execute("DELETE FROM step_subprocess WHERE node = ?", (self.i,))

    def set_resources(self, resources: dict[str, int] | None):
        self.db.execute("DELETE FROM step_resource WHERE node = ?", (self.i,))
        if resources is None:
            return
        rows = [(self.i, name, units) for name, units in resources.items()]
        self.db.executemany("INSERT INTO step_resource VALUES (?, ?, ?)", rows)

    def register_nglob(self, nglob_multi):
        data = (self.i, pickle.dumps(nglob_multi))
        self.db.execute("INSERT INTO nglob_multi(node, data) VALUES (?, ?)", data)

    #
    # Respond to graph modifications by flagging the necessary _check_* fields.
    #

    def detach(self):
        """Detach this step from the graph, but keep it in the database."""
        super().detach()
        self._check_with_products()
        # Source steps of the detached subtree lost a sink, so their metadata is stale.
        self.db.execute(RECURSIVE_CHECK_AFTER_SOURCES, (self.i,))

    def recycle(self, new_creator: Node):
        """Reconnect the node to a new creator node, preserving its properties."""
        super().recycle(new_creator)
        self._check_with_products()

    def _check_with_products(self):
        """Flag if the _check_safe and _check_after fields of this step and its products."""
        self.db.execute(RECURSIVE_CHECK_WITH_PRODUCTS, (self.i,))
