# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright 2024-2026 Toon Verstraelen
#
# This file is part of StepUp Core.
#
# StepUp Core is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# StepUp Core is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>
#
# --
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
from .file import File, FileHash
from .hash import StepHash
from .nglob import NGlobMulti
from .static_tree import StaticTree
from .stepinfo import StepInfo
from .trellis import Node
from .utils import format_digest

__all__ = ("RESERVED_ENV_VARS", "Step", "truncate_output")


logger = logging.getLogger(__name__)


# Environment variables that StepUp sets for each step (see Executor.run).
# These are managed by StepUp and must not be amended as env dependencies or set as overrides.
RESERVED_ENV_VARS = frozenset(
    {"HERE", "ROOT", "STEPUP_STEP_I", "STEPUP_STEP_INP_DIGEST", "STEPUP_STEP_NEED"}
)


STEP_SCHEMA = """
CREATE TABLE IF NOT EXISTS step (
    -- Main data
    node INTEGER PRIMARY KEY,
    -- The node of the step in the node table.
    state INTEGER NOT NULL CHECK(state >= 21 AND state <= 25),
    -- The state of the step, as defined in the StepState enum.
    need INTEGER NOT NULL CHECK(need >= 31 AND need <= 34),
    -- The need of the step, as defined in the Need enum.
    duration REAL NOT NULL CHECK(duration >= 0),
    -- An estimate of the wall time of the step in seconds.
    rescheduled_info TEXT NOT NULL,
    -- Information about why this step was rescheduled,
    -- or an empty string if it was not rescheduled.
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
    -- The need that is implied by consumers, as defined in the Need enum.
    _tail_time REAL NOT NULL CHECK(_tail_time >= 0),
    -- The tail_time of this step, defined as the total duration of the critical path from this step
    -- to the exit nodes of the workflow.
    _check_after INTEGER NOT NULL CHECK(_check_after IN (0, 1)),
    -- Whether recent changes to this step require the recalculation of the _implied_need
    -- metadata of this step and its suppliers.

    -- Indices for efficient querying
    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS step_state ON step(state);
CREATE INDEX IF NOT EXISTS step_implied_need ON step(_implied_need);
-- Partial indexes over the scheduler's "to recompute" flags. They contain only the few
-- flagged rows, so locating them each scheduling tick (see Scheduler._update_meta_*) is an
-- index scan proportional to the flagged count instead of a full scan of the step table.
CREATE INDEX IF NOT EXISTS step_check_safe ON step(node) WHERE _check_safe;
CREATE INDEX IF NOT EXISTS step_check_after ON step(node) WHERE _check_after;

CREATE TABLE IF NOT EXISTS nglob_multi (
    i INTEGER PRIMARY KEY,
    node INTEGER NOT NULL,
    data BLOB NOT NULL,
    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS nglob_multi_node ON nglob_multi(node);

CREATE TABLE IF NOT EXISTS amended_dep (
    i INTEGER PRIMARY KEY,
    FOREIGN KEY (i) REFERENCES dependency(i) ON DELETE CASCADE
) WITHOUT ROWID;

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

CREATE TABLE IF NOT EXISTS step_hash (
    node INTEGER PRIMARY KEY,
    inp_digest BLOB NOT NULL,
    inp_info BLOB,
    out_digest BLOB NOT NULL,
    out_info BLOB,
    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS step_resource (
    node  INTEGER NOT NULL,
    name  TEXT    NOT NULL CHECK(name <> ''),
    units INTEGER NOT NULL CHECK(units > 0),
    PRIMARY KEY (node, name),
    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS step_resource_name ON step_resource(name);

CREATE TABLE IF NOT EXISTS step_output (
    node    INTEGER NOT NULL,
    kind    TEXT    NOT NULL,  -- 'stdout' or 'stderr' (extensible)
    content TEXT    NOT NULL,
    PRIMARY KEY (node, kind),
    -- ON DELETE CASCADE removes these rows when the node row is deleted, matching the
    -- other satellite tables (step_hash / env_var / step_resource / step_subprocess).
    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS step_subprocess (
    node       INTEGER NOT NULL,        -- step node
    seq        INTEGER NOT NULL,        -- order of invocation within the step
    cmd        TEXT    NOT NULL,        -- shell command line
    workdir    TEXT    NOT NULL DEFAULT './',  -- relative to STEPUP_ROOT
    env_overrides TEXT,                 -- JSON-encoded dict[str, str] overlay, or NULL
    returncode INTEGER NOT NULL,
    shell      INTEGER NOT NULL DEFAULT 0,    -- 1 if cmd was run via a shell
    stdin      TEXT,                          -- standard input fed to the subprocess, or NULL
    PRIMARY KEY (node, seq),
    -- ON DELETE CASCADE removes these rows when the node row is deleted, matching the
    -- other satellite tables (step_hash / env_var / step_resource / step_output).
    -- Step.clean_before_run() still clears them explicitly between runs of a surviving step.
    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE
) WITHOUT ROWID;
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


# When a step (sub)tree is detached, the steps supplying inputs to it lose a consumer.
# Their _implied_need and _tail_time must therefore be recomputed, so flag _check_after on them.
# Suppliers are reached with two dependency hops: subtree_step <- input_file <- supplier_step.
# Only non-detached supplier steps are flagged; detached ones are excluded from the metadata anyway.
RECURSIVE_CHECK_AFTER_SUPPLIERS = """
UPDATE step SET _check_after = 1 FROM (
    WITH RECURSIVE subtree(node) AS (
        -- Start from the detached step and recurse over its product steps (the detached subtree).
        SELECT node FROM step WHERE node = ?
        UNION ALL
        SELECT i FROM node
        JOIN subtree ON node.creator = subtree.node
        WHERE node.kind = 'step'
    )
    -- Two hops back along dependency edges to the supplier steps of the subtree.
    SELECT DISTINCT dep2.supplier AS node
    FROM subtree
    JOIN dependency AS dep1 ON dep1.consumer = subtree.node
    JOIN dependency AS dep2 ON dep2.consumer = dep1.supplier
    JOIN node AS supplier_node ON supplier_node.i = dep2.supplier
    WHERE supplier_node.kind = 'step' AND NOT supplier_node.detached
) AS sup WHERE step.node = sup.node
"""


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
        """Create extra information in the database about this node."""
        self.db.execute(
            "INSERT OR REPLACE INTO step "
            "VALUES(:node, :state, :need, 1.0, '', :subshell, NULL, "
            ":safe, :check_safe, :implied_need, 1.0, :check_need)",
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
            yield "need", f"{implied_need.name} (implied by consumers > {need.name})"

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

        The satellite rows (step, env_var, nglob_multi, step_hash, step_resource,
        step_output, step_subprocess) are removed automatically by `ON DELETE CASCADE`
        when the node row is deleted, so only the dependency edges are handled here.
        """
        self.del_suppliers()
        for consumer in self.consumers(include_detached=True):
            consumer.del_suppliers([self])

    def give_up(self):
        """Clean up a detached node because it loses a product node.

        Completely remove this step, making reuse impossible.
        """
        for consumer in self.consumers(include_detached=True):
            consumer.del_suppliers([self])
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
        node_type: type = Self,
        do_suppliers: bool = True,
    ) -> Iterator[tuple[int, str]]:
        # TODO: make more efficient with executemany
        sql = "SELECT 1 FROM amended_dep WHERE i = ?"
        for idep, node_str in super()._dependencies_str(node_type, do_suppliers):
            amended = self.db.execute(sql, (idep,)).fetchone() is not None
            yield idep, f"{node_str} [amended]" if amended else node_str

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

    def set_state(self, state: StepState):
        self.db.execute(
            "UPDATE step SET state = ?, _check_safe = 1 WHERE node = ?", (state.value, self.i)
        )
        if state in (StepState.SUCCEEDED, StepState.FAILED):
            self.clear_rescheduled_info()

    def get_rescheduled_info(self) -> str:
        sql = "SELECT rescheduled_info FROM step WHERE node = ?"
        return self.db.execute(sql, (self.i,)).fetchone()[0]

    def add_rescheduled_info(self, info: str):
        self.db.execute(
            "UPDATE step SET rescheduled_info = CASE rescheduled_info"
            " WHEN '' THEN :info ELSE (rescheduled_info || '\n' || :info) END"
            " WHERE node = :i",
            {"info": info, "i": self.i},
        )

    def clear_rescheduled_info(self):
        self.db.execute("UPDATE step SET rescheduled_info = '' WHERE node = ?", (self.i,))

    def set_duration(self, duration: float):
        self.db.execute(
            "UPDATE step SET duration = ?, _check_after = 1 WHERE node = ?",
            (duration, self.i),
        )

    #
    # Get step information
    #

    def get_step_info(self) -> StepInfo:
        """Return a `StepInfo` object with information about this step.

        Amended information is not included for consistency with
        the information that is available when defining a step.
        """
        command, workdir = self.command_workdir
        return StepInfo(
            command,
            workdir,
            self.inp_paths(amended=False),
            self.env_deps(amended=False),
            self.out_paths(amended=False),
            self.vol_paths(amended=False),
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
        yield_state: bool = False,
        yield_hash: bool = False,
        yield_detached: bool = False,
        yield_amended: bool = False,
        amended: bool | None = None,
        filter_states: tuple[FileState, ...] = (),
    ) -> Iterator:
        """Iterate over paths of this step using various criteria."""
        # Which relation?
        data = {"node": self.i}
        if relation == "product":
            if yield_amended or amended is not None:
                raise ValueError("Cannot combine amended with product relation.")
            sql = "WITH relevant AS (SELECT i AS node FROM node WHERE creator = :node)"
        elif relation == "supplier":
            sql = (
                "WITH relevant AS "
                "(SELECT supplier AS node, i AS idep FROM dependency WHERE consumer = :node)"
            )
        elif relation == "consumer":
            sql = (
                "WITH relevant AS "
                "(SELECT consumer AS node, i AS idep FROM dependency WHERE supplier = :node)"
            )
        else:
            raise ValueError(f"Unrecognized relation argument: '{relation}'")
        join = "JOIN node ON node.i = relevant.node"

        # Which fields to yield?
        fields = ["label"]
        join_file = False
        if yield_state:
            fields.append("state")
            join_file = True
        if yield_hash:
            fields.extend(["digest", "mode", "mtime", "size", "inode"])
            join_file = True
        if yield_detached:
            fields.append("detached")
        if yield_amended:
            fields.append("EXISTS (SELECT 1 FROM amended_dep WHERE amended_dep.i = relevant.idep)")
        if len(filter_states) > 0:
            join_file = True
        if join_file:
            join += " JOIN file ON file.node = relevant.node"
        where = "WHERE kind = 'file'"

        # Exclude detached paths if not yielding detached
        if not yield_detached:
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
        for row in self.db.execute(sql, data):
            record = [row[0]]
            i = 1
            if yield_state:
                record.append(FileState(row[i]))
                i += 1
            if yield_hash:
                record.append(FileHash(*row[i : i + 5]))
                i += 5
            if yield_detached:
                record.append(bool(row[i]))
                i += 1
            if yield_amended:
                record.append(bool(row[i]))
            yield record[0] if len(record) == 1 else tuple(record)

    def inp_paths(
        self,
        *,
        yield_state: bool = False,
        yield_hash: bool = False,
        yield_detached: bool = False,
        yield_amended: bool = False,
        amended: bool | None = None,
    ) -> Iterator:
        """Iterate over input files of this step."""
        yield from self._paths(
            "supplier", yield_state, yield_hash, yield_detached, yield_amended, amended
        )

    def out_paths(
        self,
        *,
        yield_state: bool = False,
        yield_hash: bool = False,
        yield_detached: bool = False,
        yield_amended: bool = False,
        amended: bool | None = None,
    ) -> Iterator:
        """Iterate over output files of this step."""
        yield from self._paths(
            "consumer",
            yield_state,
            yield_hash,
            yield_detached,
            yield_amended,
            amended,
            (FileState.AWAITED, FileState.BUILT, FileState.OUTDATED),
        )

    def vol_paths(
        self,
        *,
        yield_hash: bool = False,
        yield_detached: bool = False,
        yield_amended: bool = False,
        amended: bool | None = None,
    ) -> Iterator:
        """Iterate over volatile output files of this step."""
        yield from self._paths(
            "consumer",
            False,
            yield_hash,
            yield_detached,
            yield_amended,
            amended,
            (FileState.VOLATILE,),
        )

    def static_paths(self, *, yield_hash: bool = False) -> Iterator:
        """Iterate over static paths created by this step."""
        yield from self._paths(
            "product", False, yield_hash, False, False, None, (FileState.STATIC,)
        )

    def missing_paths(self, *, yield_hash: bool = False) -> Iterator:
        """Iterate over missing paths created by this step."""
        yield from self._paths(
            "product", False, yield_hash, False, False, None, (FileState.MISSING,)
        )

    def env_deps(self, *, amended: bool | None = None, yield_amended: bool = False):
        """Iterate over used environment variable names (not values)."""
        if yield_amended:
            sql = "SELECT name, amended FROM env_var WHERE node = ?"
        else:
            sql = "SELECT name FROM env_var WHERE node = ?"
        if amended is not None:
            sql += " AND"
            if not amended:
                sql += " NOT"
            sql += " amended = 1"
        for row in self.db.execute(sql, (self.i,)):
            if yield_amended:
                yield row[0], bool(row[1])
            else:
                yield row[0]

    def nglob_multis(self) -> Iterator[NGlobMulti]:
        """Iterate of nglob_multis used by this step."""
        for row in self.db.execute("SELECT data FROM nglob_multi WHERE node = ?", (self.i,)):
            yield pickle.loads(row[0])

    #
    # Build phase
    #

    def clean_before_run(self):
        """Remove all information that is expected to be set when running a step.

        This method is called right before (re)running a step and cleans up leftovers
        that may still hang around from a previous (failed or aborted) execution.

        The following are removed:

        - reschedule_info
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
        # Drop amended suppliers.
        rows = list(
            self.db.execute(
                "SELECT dependency.i, node.i, node.label, node.kind FROM dependency "
                "JOIN node ON node.i = supplier "
                "JOIN amended_dep ON amended_dep.i = dependency.i WHERE consumer = ?",
                (self.i,),
            )
        )
        self.db.executemany("DELETE FROM amended_dep WHERE i = ?", ((row[0],) for row in rows))
        self.del_suppliers(
            [self.graph.node_classes[kind](self.graph, i, label) for _, i, label, kind in rows]
        )

        # Drop amended environment variables
        self.db.execute("DELETE FROM env_var WHERE node = ? AND amended = 1", (self.i,))

        # Drop nglob_multis
        self.db.execute("DELETE FROM nglob_multi WHERE node = ?", (self.i,))

        # Drop amended consumers and detach the corresponding consumer nodes.
        records_consumer = list(
            self.db.execute(
                "SELECT dependency.i, consumer, label, kind FROM dependency "
                "JOIN amended_dep ON amended_dep.i = dependency.i "
                "JOIN node ON consumer = node.i "
                "WHERE supplier = ?",
                (self.i,),
            )
        )
        ideps_consumer = [(row[0],) for row in records_consumer]
        self.db.executemany("DELETE FROM amended_dep WHERE i = ?", ideps_consumer)
        for _, i, label, kind in records_consumer:
            node = self.graph.node_classes[kind](self.graph, i, label)
            node.del_suppliers([self])
            node.detach()

        # Detach steps created by this step
        sql = "SELECT i, label FROM node WHERE creator = ? AND kind = 'step'"
        for i, label in self.db.execute(sql, (self.i,)):
            step = Step(self.graph, i, label)
            step.detach()

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
            file.mark_outdated()

        # Drop any output stored by a previous run.
        self.delete_outputs()

        # Drop any subprocess invocations recorded by a previous run.
        self.delete_subprocesses()

    def completed(self, new_hash: StepHash | None):
        """Set a step as completed (succeeded or failed) and trigger the consequences.

        Parameters
        ----------
        new_hash
            The new digest of the completed step if the step was successful, `None` otherwise.
        """
        if new_hash is None:
            rescheduled_info = self.get_rescheduled_info()
            # Update states, needed for files that have not changed since previous run.
            for file in self.products(File):
                if file.get_state() == FileState.BUILT:
                    file.set_state(FileState.OUTDATED)
            if rescheduled_info != "":
                logger.info("Rescheduled step: %s", self.label)
                # We just set the state to PENDING.
                # However, it will not be scheduled as long as `rescheduled_info` has some info.
                # Any later file changes relevant to the step will result in a call
                # to mark_pending(), which will clear the rescheduled_info.
                # This makes the step eligible for scheduling again.
                self.set_state(StepState.PENDING)
                self.delete_hash()
            else:
                logger.info("Failed step: %s", self.label)
                self.set_state(StepState.FAILED)
                self.delete_hash()
        else:
            logger.info("Succeeded step: %s", self.label)
            self.set_state(StepState.SUCCEEDED)
            # Update states, needed for files that have not changed since previous run.
            for file in self.products(File):
                if file.get_state() == FileState.OUTDATED:
                    file.set_state(FileState.BUILT)
                    file.completed()
            self.set_hash(new_hash)

    def get_hash(self) -> StepHash | None:
        sql = "SELECT inp_digest, inp_info, out_digest, out_info FROM step_hash WHERE node = ?"
        row = self.db.execute(sql, (self.i,)).fetchone()
        if row is None:
            return None
        return StepHash(row[0], pickle.loads(row[1]), row[2], pickle.loads(row[3]))

    def set_hash(self, step_hash: StepHash):
        data = (
            self.i,
            step_hash.inp_digest,
            pickle.dumps(step_hash.inp_info),
            step_hash.out_digest,
            pickle.dumps(step_hash.out_info),
        )
        self.db.execute("INSERT OR REPLACE INTO step_hash VALUES (?, ?, ?, ?, ?)", data)

    def delete_hash(self):
        self.db.execute("DELETE FROM step_hash WHERE node = ?", (self.i,))

    def store_output(self, kind: str, content: str, max_size: int) -> None:
        """Persist captured output of one stream for this step.

        Any previously stored row for this `(node, kind)` pair is removed first,
        so a stream that produced output on an earlier run but is empty now does not
        leave a stale row.

        Parameters
        ----------
        kind
            The stream identifier, e.g. `'stdout'` or `'stderr'`.
        content
            The full captured text (untruncated).
        max_size
            Maximum number of UTF-8 bytes to store, or `0` for unlimited.
            See `truncate_output`.
        """
        # Delete the existing row for this kind first: INSERT OR REPLACE cannot
        # remove a row whose content is now empty, so an explicit DELETE is needed.
        self.db.execute("DELETE FROM step_output WHERE node = ? AND kind = ?", (self.i, kind))
        if content:
            self.db.execute(
                "INSERT INTO step_output VALUES (?, ?, ?)",
                (self.i, kind, truncate_output(content, max_size)),
            )

    def get_output(self, kind: str) -> str:
        """Return the stored output for one stream, or an empty string if absent."""
        sql = "SELECT content FROM step_output WHERE node = ? AND kind = ?"
        row = self.db.execute(sql, (self.i, kind)).fetchone()
        return row[0] if row else ""

    def delete_outputs(self) -> None:
        """Remove all stored output rows for this step."""
        self.db.execute("DELETE FROM step_output WHERE node = ?", (self.i,))

    def record_subprocess(
        self,
        cmd: str,
        workdir: str,
        env_overrides: dict[str, str] | None,
        returncode: int,
        shell: bool = False,
        stdin: str | None = None,
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
            The standard input fed to the subprocess as a string, or `None` when no input
            was provided. Stored verbatim.
        """
        # The per-step sequence number is assigned here, under the director's DBSession,
        # so concurrent steps cannot collide and a re-run (which clears the rows first)
        # restarts the numbering at 0.
        (seq,) = self.db.execute(
            "SELECT COALESCE(MAX(seq) + 1, 0) FROM step_subprocess WHERE node = ?", (self.i,)
        ).fetchone()
        self.db.execute(
            "INSERT INTO step_subprocess "
            "(node, seq, cmd, workdir, env_overrides, returncode, shell, stdin) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.i,
                seq,
                cmd,
                workdir,
                None if env_overrides is None else json.dumps(env_overrides),
                returncode,
                int(shell),
                stdin,
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
    # Watch phase
    #

    def mark_pending(self):
        """Set SUCCEEDED or FAILED step pending (again).

        There can be many reasons for marking a step pending again, after having been completed:

        - inputs changes
        - outputs disappeared
        - environment variables changed

        As a side effect, this method is sometimes also called on RUNNING steps,
        in which case the call is ignored.

        This method also clears the rescheduled_info,
        which makes the step eligible for scheduling again.
        """
        # Note that PENDING, RUNNING, and CHECKING are ignored.
        # This method may be called on RUNNING steps that create their own amended inputs.
        # CHECKING steps are mid hash-check and will settle naturally (SUCCEEDED or PENDING).
        state = self.get_state()
        if state in (StepState.RUNNING, StepState.CHECKING):
            return
        self.clear_rescheduled_info()
        if state in (StepState.SUCCEEDED, StepState.FAILED):
            logger.info("Mark %s step PENDING: %s", state.name, self.label)
            self.set_state(StepState.PENDING)
            # Make all consumers (output files) pending
            for file in self.consumers(File, include_detached=True):
                if file.get_state() == FileState.BUILT:
                    file.mark_outdated()

    #
    # Respond to graph modifications by flagging the necessary _check_* fields.
    #

    def detach(self):
        """Detach this step from the graph, but keep it in the database."""
        super().detach()
        self._check_with_products()
        # Supplier steps of the detached subtree lost a consumer, so their metadata is stale.
        self.db.execute(RECURSIVE_CHECK_AFTER_SUPPLIERS, (self.i,))

    def recycle(self, new_creator: Node):
        """Reconnect the node to a new creator node, preserving its properties."""
        super().recycle(new_creator)
        self._check_with_products()

    def _check_with_products(self):
        """Flag if the _check_safe and _check_after fields of this step and its products."""
        self.db.execute(RECURSIVE_CHECK_WITH_PRODUCTS, (self.i,))

    def add_supplier(self, supplier: Node) -> int:
        """Add a supplier-consumer relation."""
        idep = super().add_supplier(supplier)
        self._check_simple()
        return idep

    def del_suppliers(self, suppliers: list[Node] | None = None):
        """Delete a supplier-consumer relation."""
        super().del_suppliers(suppliers)
        self._check_simple()

    def _check_simple(self):
        """Flag if the _check_after field."""
        # Only _check_after is relevant
        self.db.execute("UPDATE step SET _check_after = 1 WHERE node = ?", (self.i,))
