# StepUp Core provides the basic framework for the StepUp build tool.
# © 2024–2025 Toon Verstraelen
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
"""A `Step` an action that can be executed and that has inputs and/or outputs."""

import logging
import os
import pickle
from collections.abc import Iterator
from typing import TYPE_CHECKING, Self

import attrs
from path import Path

from .cascade import Node
from .deferred_glob import DeferredGlob
from .enums import FileState, Mandatory, StepState
from .file import File, FileHash
from .hash import StepHash
from .job import RunJob, ValidateAmendedJob
from .nglob import NGlobMulti
from .stepinfo import StepInfo
from .utils import format_digest

if TYPE_CHECKING:
    from .workflow import Workflow


__all__ = ("Step",)


logger = logging.getLogger(__name__)


STEP_SCHEMA = """
CREATE TABLE IF NOT EXISTS step (
    node INTEGER PRIMARY KEY,
    state INTEGER NOT NULL CHECK(state >= 21 AND state <= 25),
    pool TEXT,
    block INTEGER NOT NULL CHECK(block IN (0, 1)),
    mandatory INTEGER NOT NULL CHECK(mandatory >= 31 AND mandatory <= 33),
    validate_amended INTEGER NOT NULL CHECK(block IN (0, 1)),
    rescheduled_info TEXT NOT NULL,
    FOREIGN KEY (node) REFERENCES node(i)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS step_state_mandator ON step(state, mandatory);

CREATE TABLE IF NOT EXISTS pool_definition (
    node INTEGER NOT NULL,
    name TEXT NOT NULL,
    size INTEGER NOT NULL CHECK(size > 0),
    PRIMARY KEY(node, name)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS nglob_multi (
    i INTEGER PRIMARY KEY,
    node INTEGER NOT NULL,
    data BLOB NOT NULL,
    FOREIGN KEY (node) REFERENCES node(i)
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
    FOREIGN KEY (node) REFERENCES node(i)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS env_var_node ON env_var(node);

CREATE TABLE IF NOT EXISTS step_hash (
    node INTEGER PRIMARY KEY,
    inp_digest BLOB NOT NULL,
    inp_info BLOB,
    out_digest BLOB NOT NULL,
    out_info BLOB,
    FOREIGN KEY (node) REFERENCES node(i)
);
"""

EXISTS_MANDATORY_CONSUMER_STEPS = f"""
SELECT EXISTS (SELECT 1 FROM step AS step1
JOIN dependency AS dep1 ON step1.node = dep1.supplier
JOIN file ON dep1.consumer = file.node
JOIN dependency AS dep2 ON file.node = dep2.supplier
JOIN step AS step2 ON step2.node = dep2.consumer
JOIN node AS node2 ON node2.i = dep2.consumer
WHERE step1.node = ?
AND step2.mandatory != {Mandatory.NO.value}
AND NOT node2.orphan
)
"""

SELECT_SUPPLYING_STEPS = """
SELECT node.i, node.label FROM node
JOIN step ON node.i = step.node
JOIN dependency AS dep1 ON dep1.supplier = node.i
JOIN file ON file.node = dep1.consumer
JOIN dependency AS dep2 ON dep2.supplier = dep1.consumer
WHERE dep2.consumer = ?
"""

# Check if any of the (recursive) creators of a step does not allow it to be queued yet.
HAS_UNCERTAIN_CREATORS = f"""
WITH RECURSIVE trace(i, label, creator, certain) AS (
    SELECT i, label, creator,
    state in ({StepState.RUNNING.value}, {StepState.SUCCEEDED.value})
    FROM node JOIN step ON node.i = step.node
    WHERE i = (SELECT creator FROM node WHERE i = ?)
    UNION
    SELECT node.i, node.label, node.creator,
    step.state in ({StepState.RUNNING.value}, {StepState.SUCCEEDED.value})
    FROM node JOIN step ON node.i = step.node JOIN trace ON trace.creator = node.i
    WHERE trace.certain AND node.kind = 'step'
)
SELECT EXISTS (SELECT 1 FROM trace WHERE NOT certain)
"""

# Find all product steps (recursively) that are pending, without recursing past pending steps.
# Only mandatory or required steps are considered.
RECURSE_PRODUCTS_PENDING = f"""
WITH RECURSIVE tree(i, label, creator, state) AS (
    SELECT i, label, creator, state FROM node
    JOIN step ON node.i = step.node
    WHERE creator = ? AND step.mandatory != {Mandatory.NO.value}
    UNION
    SELECT node.i, node.label, node.creator, step.state FROM node
    JOIN step ON node.i = step.node JOIN tree ON node.creator = tree.i
    WHERE tree.state != {StepState.PENDING.value} AND step.mandatory != {Mandatory.NO.value}
)
SELECT i, label FROM tree WHERE state = {StepState.PENDING.value}
"""


def split_step_label(label: str) -> tuple[str, str]:
    """Split a step label into action and workdir."""
    parts = label.split("  # wd=", maxsplit=1)
    return parts[0], Path(parts[1]) if len(parts) == 2 else Path("./")


@attrs.define
class Step(Node):
    @property
    def workflow(self) -> "Workflow":
        return self.cascade

    #
    # Override from base class
    #

    @classmethod
    def schema(cls) -> str | None:
        """Return node-specific SQL commands to initialize the database."""
        return STEP_SCHEMA

    @classmethod
    def create_label(cls, label: str, action: str, workdir: str, **kwargs):
        """Optionally override the user-provided label when creating a node."""
        if label != "":
            raise ValueError(
                "Do not provide a label when creating a step. "
                "It will be derived from other arguments."
            )
        if "  # wd=" in action:
            raise ValueError("Do not provide a workdir comment in the action string.")
        label = action
        if workdir != "./":
            label += f"  # wd={workdir}"
        return label

    def initialize(
        self,
        pool: str | None = None,
        block: bool = False,
        mandatory: Mandatory = Mandatory.YES,
        **kwargs,
    ):
        """Create extra information in the database about this node."""
        self.con.execute(
            "INSERT OR REPLACE INTO step VALUES(:node, :state, :pool, :block, :mandatory, 1, '')",
            {
                "node": self.i,
                "pool": pool,
                "block": int(block),
                "state": StepState.PENDING.value,
                "mandatory": mandatory.value,
            },
        )

    def validate(self):
        """Validate extra information about this node is present in the database."""
        row = self.con.execute("SELECT 1 FROM step WHERE node = ?", (self.i,)).fetchone()
        if row is None:
            raise ValueError(f"Step node {self.key()} has no row in the file table.")

    def format_properties(self) -> Iterator[tuple[str, str]]:
        """Iterate over key-value pairs that represent the properties of the node."""
        state, pool, block, mandatory, _ = self.properties()
        yield "state", state.name
        if mandatory != Mandatory.YES:
            yield "mandatory", mandatory.name
        if pool is not None:
            yield "pool", pool
        if block:
            yield "block", block

        sql = "SELECT name, amended FROM env_var WHERE node = ?"
        label = "env_var"
        for env_var, amended in self.con.execute(sql, (self.i,)):
            yield label, f"{env_var} [amended]" if amended else env_var
            label = ""

        for row in self.con.execute("SELECT data FROM nglob_multi WHERE node = ?", (self.i,)):
            ngm = pickle.loads(row[0])
            yield "ngm", f"{[ngs.pattern for ngs in ngm.nglob_singles]} {ngm.subs}"

        for pool, size in self.pool_definitions():
            yield "defines pool", f"{pool}={size}"

        step_hash = self.get_hash()
        if step_hash is not None:
            l1, l2 = format_digest(step_hash.inp_digest)
            yield "inp_digest", l1
            yield "", l2
            l1, l2 = format_digest(step_hash.out_digest)
            yield "out_digest", l1
            yield "", l2
            if step_hash.inp_info is not None:
                yield "explained", "yes"

    def _make_orphan(self):
        """Update the node or the graph when it becomes orphan."""
        # This step can no longer be required.
        self.undo_required()
        # Also check whether supplying steps are no longer be required.
        for i, label in self.con.execute(SELECT_SUPPLYING_STEPS, (self.i,)):
            step = Step(self.cascade, i, label)
            step.undo_required()

    def _undo_orphan(self):
        """Update the node or the graph when the node is recreated."""
        # This step may become required.
        self.make_required()
        # Also check if supplying steps become required.
        for i, label in self.con.execute(SELECT_SUPPLYING_STEPS, (self.i,)):
            step = Step(self.cascade, i, label)
            step.make_required()

    def clean(self):
        """Perform a cleanup right before the orphaned node is removed from the graph."""
        self.del_suppliers()
        for consumer in self.consumers(include_orphans=True):
            consumer.del_suppliers([self])
        self.con.execute("DELETE FROM step WHERE node = ?", (self.i,))
        self.con.execute("DELETE FROM env_var WHERE node = ?", (self.i,))
        self.con.execute("DELETE FROM nglob_multi WHERE node = ?", (self.i,))
        self.con.execute("DELETE FROM pool_definition WHERE node = ?", (self.i,))
        self.con.execute("DELETE FROM step_hash WHERE node = ?", (self.i,))

    def add_supplier(self, supplier: Node) -> int:
        """Add a supplier-consumer relation.

        Parameters
        ----------
        supplier
            Other node that supplies to thise node.

        Returns
        -------
        idep
            The identifier in the dependency table.
        """
        idep = super().add_supplier(supplier)
        if self.get_mandatory() != Mandatory.NO:
            for step in supplier.suppliers(Step):
                step.make_required()
        return idep

    def del_suppliers(self, suppliers: list[Node] | None = None):
        """Delete given suppliers.

        Without arguments, all suppliers of the current node are deleted.
        """
        # Get a list of suppliers to process if needed.
        # It is better not to pass this list to the super method,
        # because it will just to the same in a less efficient way.
        _suppliers = suppliers
        if suppliers is None:
            _suppliers = list(self.suppliers(include_orphans=True))
        # Call the super method to remove the actual dependencies
        super().del_suppliers(suppliers)
        # Update the mandatory status of the step and propagate to other steps.
        if self.get_mandatory() != Mandatory.NO:
            steps = set()
            for supplier in _suppliers:
                for step in supplier.suppliers(Step):
                    steps.add(step)
            for step in steps:
                step.undo_required()

    def detach(self):
        """Clean up an orphaned node because it loses a product node.

        Completely remove this step, making reuse impossible.
        """
        for consumer in self.consumers(include_orphans=True):
            consumer.del_suppliers([self])
        for product in self.products():
            product.orphan()
        self.orphan()
        self.clean()
        self.con.execute("DELETE FROM node WHERE i = ?", (self.i,))

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
            amended = self.con.execute(sql, (idep,)).fetchone() is not None
            yield idep, f"{node_str} [amended]" if amended else node_str

    def properties(self) -> tuple[str, Path, StepState, str, bool, Mandatory]:
        row = self.con.execute(
            "SELECT state, pool, block, mandatory, validate_amended FROM step WHERE node = ?",
            (self.i,),
        ).fetchone()
        state_i, pool, block, mandatory_i, validate_amended = row
        return (
            StepState(state_i),
            pool,
            bool(block),
            Mandatory(mandatory_i),
            bool(validate_amended),
        )

    #
    # Getters and setters
    #

    def get_action_workdir(self) -> tuple[str, str]:
        """Return the action and workdir of this step."""
        return split_step_label(self.label)

    def get_state(self) -> StepState:
        row = self.con.execute("SELECT state FROM step WHERE node = ?", (self.i,)).fetchone()
        return StepState(row[0])

    def set_state(self, state: StepState):
        self.con.execute("UPDATE step SET state = ? WHERE node = ?", (state.value, self.i))

    def get_rescheduled_info(self) -> str:
        sql = "SELECT rescheduled_info FROM step WHERE node = ?"
        return self.con.execute(sql, (self.i,)).fetchone()[0]

    def set_rescheduled_info(self, info: str):
        self.con.execute("UPDATE step SET rescheduled_info = ? WHERE node = ?", (info, self.i))

    def get_validate_amended(self) -> bool:
        sql = "SELECT validate_amended FROM step WHERE node = ?"
        return bool(self.con.execute(sql, (self.i,)).fetchone()[0])

    def set_validate_amended(self, value: bool = True):
        sql = "UPDATE step SET validate_amended = ? WHERE node = ?"
        self.con.execute(sql, (int(value), self.i))

    #
    # Get step information
    #

    def get_step_info(self) -> StepInfo:
        """Return a `StepInfo` object with information about this step.

        Amended information is not included for consistency with
        the information that is available when defining a step.
        """
        action, workdir = self.get_action_workdir()
        return StepInfo(
            action,
            workdir,
            self.inp_paths(amended=False),
            self.env_vars(amended=False),
            self.out_paths(amended=False),
            self.vol_paths(amended=False),
        )

    #
    # Mandatory / Optional
    #

    def get_mandatory(self) -> Mandatory:
        row = self.con.execute("SELECT mandatory FROM step WHERE node = ?", (self.i,)).fetchone()
        return Mandatory(row[0])

    def set_mandatory(self, mandatory: Mandatory):
        self.con.execute("UPDATE step SET mandatory = ? WHERE node = ?", (mandatory.value, self.i))

    def make_required(self):
        """Try to set this step to Mandatory.REQUIRED and propagate to other optional suppliers."""
        # Scan all direct step dependencies for mandatory or required ones.
        logger.info(
            "Number of mandatory consumer steps: %s",
            self.con.execute(EXISTS_MANDATORY_CONSUMER_STEPS, (self.i,)).fetchone()[0],
        )
        if (
            self.get_mandatory() == Mandatory.NO
            and not self.is_orphan()
            and self.con.execute(EXISTS_MANDATORY_CONSUMER_STEPS, (self.i,)).fetchone()[0] > 0
        ):
            self.set_mandatory(Mandatory.REQUIRED)
            for i, label in self.con.execute(SELECT_SUPPLYING_STEPS, (self.i,)):
                step = Step(self.cascade, i, label)
                step.make_required()
            self.queue_if_appropriate()

    def undo_required(self):
        """Try to set this node to Mandatory.NO and propagate to other optional suppliers."""
        if self.get_mandatory() == Mandatory.REQUIRED and (
            self.is_orphan()
            or self.con.execute(EXISTS_MANDATORY_CONSUMER_STEPS, (self.i,)).fetchone()[0] == 0
        ):
            self.set_mandatory(Mandatory.NO)
            for i, label in self.con.execute(SELECT_SUPPLYING_STEPS, (self.i,)):
                step = Step(self.cascade, i, label)
                step.undo_required()

    #
    # Env vars
    #

    def add_env_vars(self, env_vars):
        rows = [(self.i, name, os.getenv(name)) for name in env_vars]
        self.con.executemany("INSERT OR REPLACE INTO env_var VALUES (?, ?, ?, 0)", rows)

    def amend_env_vars(self, env_vars):
        rows = [(self.i, name, os.getenv(name)) for name in env_vars]
        self.con.executemany("INSERT OR IGNORE INTO env_var VALUES (?, ?, ?, 1)", rows)

    #
    # Pool definitions
    #

    def define_pool(self, pool: str, size: int):
        # If the pool has already been defined, it should have the same size.
        sql = "SELECT size FROM pool_definition WHERE name = ?"
        for (old_size,) in self.con.execute(sql, (pool,)):
            # Checking one is in principle sufficient, but let's play it safe an check them all.
            if old_size != size:
                raise ValueError(
                    f"Pool with old size {old_size} defined with different new size {size}"
                )
        self.con.execute(
            "INSERT OR IGNORE INTO pool_definition VALUES (?, ?, ?)", (self.i, pool, size)
        )

    #
    # Iterators
    #

    def _paths(
        self,
        relation: str,
        yield_state: bool = False,
        yield_hash: bool = False,
        yield_orphan: bool = False,
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
        if yield_orphan:
            fields.append("orphan")
        if yield_amended:
            fields.append("EXISTS (SELECT 1 FROM amended_dep WHERE amended_dep.i = relevant.idep)")
        if len(filter_states) > 0:
            join_file = True
        if join_file:
            join += " JOIN file ON file.node = relevant.node"
        where = "WHERE kind = 'file'"

        # Exclude orphaned paths if not yielding orphan
        if not yield_orphan:
            where += " AND NOT orphan"

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
        for row in self.con.execute(sql, data):
            record = [row[0]]
            i = 1
            if yield_state:
                record.append(FileState(row[i]))
                i += 1
            if yield_hash:
                record.append(FileHash(*row[i : i + 5]))
                i += 5
            if yield_orphan:
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
        yield_orphan: bool = False,
        yield_amended: bool = False,
        amended: bool | None = None,
    ) -> Iterator:
        """Iterate over input files of this step."""
        yield from self._paths(
            "supplier", yield_state, yield_hash, yield_orphan, yield_amended, amended
        )

    def out_paths(
        self,
        *,
        yield_state: bool = False,
        yield_hash: bool = False,
        yield_orphan: bool = False,
        yield_amended: bool = False,
        amended: bool | None = None,
    ) -> Iterator:
        """Iterate over output files of this step."""
        yield from self._paths(
            "consumer",
            yield_state,
            yield_hash,
            yield_orphan,
            yield_amended,
            amended,
            (FileState.AWAITED, FileState.BUILT, FileState.OUTDATED),
        )

    def vol_paths(
        self,
        *,
        yield_hash: bool = False,
        yield_orphan: bool = False,
        yield_amended: bool = False,
        amended: bool | None = None,
    ) -> Iterator:
        """Iterate over volatile output files of this step."""
        yield from self._paths(
            "consumer",
            False,
            yield_hash,
            yield_orphan,
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

    def env_vars(self, *, amended: bool | None = None, yield_amended: bool = False):
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
        for row in self.con.execute(sql, (self.i,)):
            if yield_amended:
                yield row[0], bool(row[1])
            else:
                yield row[0]

    def nglob_multis(self) -> Iterator[NGlobMulti]:
        """Iterate of nglob_multis used by this step."""
        for row in self.con.execute("SELECT data FROM nglob_multi WHERE node = ?", (self.i,)):
            yield pickle.loads(row[0])

    def pool_definitions(self):
        sql = "SELECT name, size FROM pool_definition WHERE node = ?"
        yield from self.con.execute(sql, (self.i,))

    #
    # Run phase
    #

    def queue_if_appropriate(self):
        state, pool, block, mandatory, validate_amended = self.properties()

        # Check basics that would prohibit scheduling impossible.
        if block:
            return
        if mandatory == Mandatory.NO:
            return
        if self.is_orphan():
            return
        if state != StepState.PENDING:
            return

        # Do not start a step if any of its (recursive) creators are not in a valid state.
        # These may still produce or changes files relevant for this step.
        if self.con.execute(HAS_UNCERTAIN_CREATORS, (self.i,)).fetchone()[0]:
            return

        # Check whether initial and amended inputs are ready.
        # Also collect the input hashes for input validation before the step is started.
        amended_inputs_ready = True
        inp_hashes = []
        sql = (
            "SELECT node.label, node.orphan, file.state, "
            "EXISTS (SELECT 1 FROM amended_dep WHERE amended_dep.i = dep.i), "
            "file.digest, file.mode, file.mtime, file.size, file.inode "
            "FROM node JOIN dependency AS dep ON node.i = dep.supplier "
            "JOIN file ON file.node = node.i "
            "WHERE dep.consumer = ?"
        )
        cursor = self.con.execute(sql, (self.i,))
        for path, is_orphan, fs_value, is_amended, digest, mode, mtime, size, inode in cursor:
            file_state = FileState(fs_value)
            if is_orphan or file_state not in (FileState.BUILT, FileState.STATIC):
                if is_amended and validate_amended:
                    amended_inputs_ready = False
                else:
                    # We are sure that the step cannot be started due to one of two conditions:
                    # - Initial input is not ready.
                    # - Amended input is not ready and not going to validate it.
                    return
            else:
                inp_hashes.append((path, FileHash(digest, mode, mtime, size, inode)))

        # Determine the appropriate job to queue.
        step_hash = self.get_hash()
        # Get a list of environment variables used.
        env_vars = list(self.env_vars())

        if amended_inputs_ready or step_hash is None:
            # All (amended) inputs are ready, or the job is not skippable.
            job = RunJob(self, pool, inp_hashes, env_vars, step_hash)
        else:
            # If the initial inputs are ready, but the amended inputs are not,
            # and there is a step hash, we need to validate the amended inputs first.
            # If they are not available, and if the existing inputs have changed,
            # they may also no longer be needed.
            job = ValidateAmendedJob(self, pool, inp_hashes, env_vars, step_hash)

        # Queue the job.
        logger.info("Queue %s", job.name)
        self.set_state(StepState.QUEUED)
        self.set_rescheduled_info("")
        self.set_validate_amended(False)
        self.workflow.job_queue.put_nowait(job)
        self.workflow.job_queue_changed.set()

    def clean_before_run(self):
        """Remove all inforation that is expected to be set when running a step.

        This method is called right before (re)running a step and cleans up leftovers
        that may still hang around from a previous (failed or aborted) execution.

        The following are removed:

        - reschedule_info
        - amended inputs and (volatile outputs)
        - amended environment variables
        - pool definitions

        The following are orphaned:

        - nglob_multis
        - created steps
        - static file definitions
        - deferred globs

        The following are marked as outdated:

        - output files that are in state BUILT
        """
        # Clear rescheduled_info
        self.set_rescheduled_info("")

        # Drop amended suppliers.
        rows = list(
            self.con.execute(
                "SELECT dependency.i, node.i, node.label, node.kind FROM dependency "
                "JOIN node ON node.i = supplier "
                "JOIN amended_dep ON amended_dep.i = dependency.i WHERE consumer = ?",
                (self.i,),
            )
        )
        self.con.executemany("DELETE FROM amended_dep WHERE i = ?", ((row[0],) for row in rows))
        self.del_suppliers(
            [self.cascade.node_classes[kind](self.workflow, i, label) for _, i, label, kind in rows]
        )

        # Drop amended environment variables
        self.con.execute("DELETE FROM env_var WHERE node = ? AND amended = 1", (self.i,))

        # Drop pool definitions
        self.con.execute("DELETE FROM pool_definition WHERE node = ?", (self.i,))

        # Drop nglob_multis
        self.con.execute("DELETE FROM nglob_multi WHERE node = ?", (self.i,))

        # Drop amended consumers and orphan the corresponding consumer nodes.
        records_consumer = list(
            self.con.execute(
                "SELECT dependency.i, consumer, label, kind FROM dependency "
                "JOIN amended_dep ON amended_dep.i = dependency.i "
                "JOIN node ON consumer = node.i "
                "WHERE supplier = ?",
                (self.i,),
            )
        )
        ideps_consumer = [(row[0],) for row in records_consumer]
        self.con.executemany("DELETE FROM amended_dep WHERE i = ?", ideps_consumer)
        for _, i, label, kind in records_consumer:
            node = self.cascade.node_classes[kind](self.cascade, i, label)
            node.del_suppliers([self])
            node.orphan()

        # Orphan steps created by this step
        sql = "SELECT i, label FROM node WHERE creator = ? AND kind = 'step'"
        for i, label in self.con.execute(sql, (self.i,)):
            step = Step(self.workflow, i, label)
            step.orphan()

        # Orphan static file definitions
        sql = (
            "SELECT i, label FROM node JOIN file ON node.i = file.node "
            "WHERE creator = ? AND state in (?, ?)"
        )
        data = (self.i, FileState.STATIC.value, FileState.MISSING.value)
        for i, label in self.con.execute(sql, data):
            file = File(self.workflow, i, label)
            file.orphan()

        # Orphan deferred globs
        sql = "SELECT i, label FROM node WHERE creator = ? AND kind = 'dg'"
        for i, label in self.con.execute(sql, (self.i,)):
            dg = DeferredGlob(self.workflow, i, label)
            dg.orphan()

        # Mark BUILT outputs OUTDATED.
        sql = (
            "SELECT i, label FROM node JOIN file ON node.i = file.node "
            "WHERE creator = ? AND state = ?"
        )
        data = (self.i, FileState.BUILT.value)
        for i, label in self.con.execute(sql, data):
            file = File(self.workflow, i, label)
            file.mark_outdated()

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
                self.set_state(StepState.PENDING)
                self.delete_hash()
                # The missing inputs may have appeared by the time the step ended,
                # so we need to check if we can put the step back on the queue right away.
                self.queue_if_appropriate()
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
                    file.release_pending()
            # Pending steps that are created by this step may possibly be queued.
            # Such steps need to be searched recursively, because they are only queued
            # when all their (recursive) creators are in a valid state.
            # (This is mostly relevant for skipped steps.)
            for i, label in self.con.execute(RECURSE_PRODUCTS_PENDING, (self.i,)):
                step = Step(self.workflow, i, label)
                step.queue_if_appropriate()
            self.set_hash(new_hash)

    def get_hash(self) -> StepHash | None:
        sql = "SELECT inp_digest, inp_info, out_digest, out_info FROM step_hash WHERE node = ?"
        row = self.con.execute(sql, (self.i,)).fetchone()
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
        self.con.execute("INSERT OR REPLACE INTO step_hash VALUES (?, ?, ?, ?, ?)", data)

    def delete_hash(self):
        self.con.execute("DELETE FROM step_hash WHERE node = ?", (self.i,))

    def register_nglob(self, nglob_multi):
        data = (self.i, pickle.dumps(nglob_multi))
        self.con.execute("INSERT INTO nglob_multi(node, data) VALUES (?, ?)", data)

    #
    # Watch phase
    #

    def mark_pending(self, *, input_changed: bool = False):
        """Set succeeded or failed step pending (again).

        There can be many reasons for making a step pending,
        e.g. inputs changes, outputs disappeared, environment variables changed.
        This is also called when orphaned steps are recreated whose inputs are no longer valid.

        Parameters
        ----------
        input_changed
            Set to True when one of the inputs or environment variables (may) have changed.
            The changes will be checked and if relevant, amended step arguments will be
            refreshed, because they can be affected by the changes in inputs.
        """
        if input_changed:
            self.set_validate_amended()
        state = self.get_state()
        if state == StepState.RUNNING:
            raise RuntimeError("Cannot make a running step pending")
        if state in (StepState.SUCCEEDED, StepState.FAILED):
            logger.info("Mark %s step PENDING: %s", state.name, self.label)
            self.set_state(StepState.PENDING)
            # First make all consumers (output files) pending
            for file in self.consumers(File):
                if file.get_state() == FileState.BUILT:
                    file.mark_outdated()
