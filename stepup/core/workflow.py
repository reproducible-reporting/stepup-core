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
"""The `Workflow` is a `Cascade` subclass with more concrete node implementations."""

import asyncio
import json
import logging
import os
import pickle
import stat
import textwrap
from collections.abc import Collection, Iterator

import attrs
from path import Path

from .cascade import Cascade, Node
from .enums import FileState, Need, StepState
from .exceptions import GraphError
from .file import File
from .hash import FileHash, fmt_digest
from .nglob import NGlobMulti, has_wildcards
from .sqlite3 import UInt64, escape_like_pattern
from .static_root import StaticRoot
from .step import Step
from .utils import string_to_bool

__all__ = ("Workflow",)


logger = logging.getLogger(__name__)


# Find all inputs of steps (recursively through creator-product relations) that are missing,
# and whose creator is a static root.
RECURSE_DEFERRED_INPUTS = f"""
WITH RECURSIVE missing(i, label, creator_kind) AS (
    SELECT node.i, node.label, cnode.kind FROM node
    JOIN node AS cnode ON node.creator = cnode.i
    JOIN file ON node.i = file.node
    JOIN dependency ON node.i = supplier
    WHERE consumer = ? AND node.kind = 'file' AND file.state = {FileState.MISSING.value}
    UNION
    SELECT node.i, node.label, cnode.kind FROM node
    JOIN node AS cnode ON node.creator = cnode.i
    JOIN file ON node.i = file.node
    JOIN dependency ON node.i = supplier
    JOIN missing ON consumer = missing.i
    WHERE node.kind = 'file' AND file.state = {FileState.MISSING.value}
)
SELECT i, label FROM missing WHERE creator_kind = 'sr'
"""

# Recursively find all product steps and finally select those whose inputs are awaited or outdated.
RECURSE_OUTDATED_STEPS = f"""
WITH RECURSIVE outdated(i, label) AS (
    SELECT node.i, node.label FROM node
    WHERE node.i = ?
    UNION
    SELECT product_node.i, product_node.label FROM node AS product_node
    JOIN outdated ON product_node.creator = outdated.i
    WHERE product_node.kind = 'step'
)
SELECT DISTINCT outdated.i, outdated.label FROM outdated
JOIN dependency ON dependency.consumer = outdated.i
JOIN file ON dependency.supplier = file.node
WHERE file.state IN ({FileState.AWAITED.value}, {FileState.OUTDATED.value})
"""


@attrs.define
class SupplyInfo:
    """Result of the `supply_file` method, for internal use only."""

    file: Node = attrs.field()
    """A new or existing file."""

    available: bool = attrs.field()
    """True if possibly available, False if the certainly unavailable.

    If False, the file is AWAITED, OUTDATED or VOLATILE, and thus certainly unavailable.
    If True, the file is BUILT, MISSING or STATIC.
    In case of a MISSING file, it still needs to be confirmed as STATIC,
    but we cannot report it as unavailable yet, hence the True value.
    """

    deferred: list[Node] = attrs.field()
    """A list of MISSING file nodes whose existence and validity must be checked.

    These are typically new matches of a static root.
    """

    new_idep: int | None = attrs.field()
    """Dependency identifier when the relation is new, None otherwise."""


@attrs.define(eq=False)
class Workflow(Cascade):
    makedirs: bool = attrs.field(kw_only=True, default=True)
    """Whether to create parent directories of output files when they are supplied or created."""

    dir_queue: asyncio.Queue | None = attrs.field(kw_only=True)
    """Directories to be (un)watched can be added to this queue."""

    to_be_deleted: list[tuple[str, FileHash | None]] = attrs.field(init=False, factory=list)
    """A list of files and directories that can be deleted.

    This list contains BUILT files node with file hashes that were removed from the graph.
    """

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
            "WHERE state IN (?, ?, ?) and digest = X'75'"
        )
        data = (FileState.BUILT.value, FileState.OUTDATED.value, FileState.STATIC.value)
        files = []
        file_hashes = []
        for i, file_state_value, path in self.con.execute(sql, data):
            file_state = FileState(file_state_value)
            if strict:
                raise GraphError(f"{file_state.name} file without hash: {path}")
            logger.error(f"{file_state.name} file without hash: %s", path)
            files.append(File(self, i, path))
            file_hashes.append((path, FileHash.unknown().regen(path)))
        if len(file_hashes) > 0:
            logger.error("Fixing %s file hashes", len(file_hashes))
            self.update_file_hashes(file_hashes, "external")
            for file in self.files:
                file.mark_outdated()

        # Verify that all succeeded steps only have BUILT outputs.
        sql = (
            "SELECT file.state, fnode.label, snode.i, snode.label FROM node AS fnode "
            "JOIN file ON fnode.i = file.node JOIN dependency ON fnode.i = consumer "
            "JOIN node AS snode ON snode.i = supplier JOIN step ON step.node = snode.i "
            "WHERE step.state = ? AND file.state NOT IN (?, ?) AND NOT fnode.detached"
        )
        data = (StepState.SUCCEEDED.value, FileState.BUILT.value, FileState.VOLATILE.value)
        to_mark_pending = set()
        for file_state_value, flabel, si, slabel in self.con.execute(sql, data):
            file_state = FileState(file_state_value)
            if strict:
                raise GraphError(
                    f"{file_state.name} output of succeeded step: path_out={flabel} step={slabel}"
                )
            logger.error(
                "%s output of succeeded step: path_out=%s step=%s", file_state.name, flabel, slabel
            )
            to_mark_pending.add(Step(self, si, slabel))

        # Verify that succeeded steps have no rescheduled info.
        sql = (
            "SELECT step.node, node.label FROM step JOIN node ON step.node = node.i "
            "WHERE step.state = ? AND step.rescheduled_info != '' AND NOT node.detached"
        )
        data = (StepState.SUCCEEDED.value,)
        for si, slabel in self.con.execute(sql, data):
            if strict:
                raise GraphError(f"Rescheduled succeeded step: step={slabel}")
            logger.error("Rescheduled succeeded step: step=%s", slabel)
            to_mark_pending.add(Step(self, si, slabel))

        # Mark steps pending to rerun steps that seem to be out of date,
        # despite being marked succeeded.
        for step in to_mark_pending:
            step.mark_pending()

    def initialize_boot(self) -> bool:
        """Initialize the (new) boot script.

        Returns
        -------
        initialized
            Whether the boot script was (re)initialized.
        """
        command = "./plan.py"
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
        to_check = self.declare_missing(self.root, ["plan.py"])
        checked = [(path, file_hash.regen(path)) for path, file_hash in to_check]
        self.update_file_hashes(checked, "confirmed")
        self.define_step(self.root, command, inp_paths=["plan.py"], need=Need.PLAN, safe=True)
        return True

    @staticmethod
    def default_node_classes() -> list[type[Node]]:
        return [*Cascade.default_node_classes(), File, Step, StaticRoot]

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
        for i, kind, label in self._con.execute(node_sql):
            if label == "":
                label = kind
            label = json.dumps(textwrap.fill(label, 20))
            if kind == "step":
                props = ""
            elif kind == "file":
                props = " shape=rect fillcolor=9"
            elif kind == "sr":
                props = " shape=octagon fillcolor=7"
            else:
                props = " shape=hexagon fillcolor=6"
            lines.append(f"  {i} [label={label}{props}]")
        for i, j in self.con.execute(edge_sql):
            lines.append(f"  {i} -> {j}")
        lines.append("}")
        return "\n".join(lines)

    def format_dot_provenance(self) -> str:
        """Return the provenance graph (creator->product) in GraphViz DOT format."""
        node_sql = "SELECT i, kind, label FROM node"
        edge_sql = "SELECT creator, i FROM node"
        return self._format_dot_generic("empty", node_sql, edge_sql)

    def format_dot_dependency(self) -> str:
        """Return the dependency graph (supplier-product) in GraphViz DOT format."""
        return self._format_dot_generic(
            "normal",
            "SELECT i, kind, label FROM node WHERE NOT (kind = 'root')",
            "SELECT supplier, consumer FROM dependency "
            "JOIN node AS snode ON snode.i = supplier "
            "JOIN node AS cnode ON cnode.i = consumer "
            "WHERE NOT ((snode.kind = 'file' AND snode.label LIKE '%/')"
            "OR (cnode.kind = 'file' AND cnode.label LIKE '%/'))",
        )

    def get_file_counts(self) -> dict[FileState, int]:
        """Return counters for FileState."""
        sql = (
            "SELECT file.state, count(*) FROM node JOIN file ON node.i = file.node "
            "WHERE NOT node.detached GROUP BY file.state"
        )
        return {FileState(value): count for value, count in self.con.execute(sql)}

    def get_step_counts(self) -> dict[StepState, int]:
        """Return counters for StepState."""
        sql = (
            "SELECT step.state, count(*) FROM node JOIN step ON node.i = step.node "
            "WHERE NOT node.detached GROUP BY step.state"
        )
        return {StepState(value): count for value, count in self.con.execute(sql)}

    def steps(self, state: StepState) -> Iterator[Step]:
        sql = (
            "SELECT i, label FROM node JOIN step ON node.i = step.node "
            "WHERE state = ? AND NOT detached"
        )
        for i, label in self.con.execute(sql, (state.value,)):
            yield Step(self, i, label)

    def detached_inp_paths(self) -> Iterator[str, FileState]:
        """Iterate over detached input paths used by non-detached steps."""
        sql = (
            "SELECT node.label, file.state FROM node JOIN file ON node.i = file.node "
            "WHERE node.detached "
            "AND EXISTS (SELECT 1 FROM dependency JOIN node ON node.i = dependency.consumer "
            "WHERE supplier = file.node AND not node.detached)"
        )
        for row in self.con.execute(sql):
            yield row[0], FileState(row[1])

    def missing_paths(self) -> Iterator[str]:
        """Iterate over static files that have been deleted or were never confirmed."""
        sql = (
            "SELECT label FROM node JOIN file ON node.i = file.node "
            "WHERE state = ? AND NOT detached"
        )
        for row in self.con.execute(sql, (FileState.MISSING.value,)):
            yield row[0]

    #
    # Build phase
    #

    def _check_node(self, node: Node, argname: str, allow_root=False):
        """Check the creator or step key in the methods below."""
        if not isinstance(node, Node):
            raise TypeError(f"{argname} must be a Node instance, got: {type(node)}")
        if allow_root and node.i == self.root.i:
            return
        if node.kind() not in ("step", "sr"):
            allowed = "step, sr or root" if allow_root else "step or sr"
            raise ValueError(f"{argname} is not a {allowed}: '{node.key()}'")
        if node.is_detached():
            raise ValueError(f"{argname} is detached: '{node.key()}'")

    def matching_static_root(self, path: str) -> StaticRoot | None:
        srs = []
        sql = (
            "SELECT i, label FROM node WHERE kind = 'sr' AND NOT detached AND "
            "label = substr(?, 1, length(label))"
        )
        for i, label in self.con.execute(sql, (path,)):
            srs.append(StaticRoot(self, i, label))
        if len(srs) > 1:
            raise GraphError(f"Multiple static roots match: {path}")
        if len(srs) == 1:
            return srs[0]
        return None

    def supply_file(self, node: Node, path: str, new: bool = True) -> SupplyInfo:
        """Find an existing file or create a detached file, and make it a supplier of node.

        Parameters
        ----------
        node
            The file or step node to supply to.
        path
            The path of the file that should supply to the node.
        new
            When `True` the (file, node) relationship must be new.
            If not, a `GraphError` is raised.

        Returns
        -------
        supply_info
            Information about the supplied file object.

        Raises
        ------
        GraphError
            When the path is volatile.
            When the path exists while it is expected to be new.
        """
        available = False
        file, detached = self.find_detached(File, path)
        deferred = []
        if file is None or detached:
            sr = self.matching_static_root(path)
            if sr is None:
                file = self.create(File, None, path, state=FileState.AWAITED)
            else:
                file = self.create(File, sr, path, state=FileState.MISSING)
                deferred.append(file)
                available = True
            self.put_dir_queue(Path(path).parent)
        else:
            state = file.get_state()
            if state == FileState.VOLATILE:
                raise GraphError(f"Input is volatile: {path}")
            available = state in (FileState.BUILT, FileState.STATIC, FileState.MISSING)
            if state == FileState.MISSING:
                deferred.append(file)
        new_relation = (
            self.con.execute(
                "SELECT 1 FROM dependency WHERE supplier = ? AND consumer = ?", (file.i, node.i)
            ).fetchone()
            is None
        )
        if new_relation:
            new_idep = node.add_supplier(file)
        elif new:
            raise GraphError(f"Supplying file already exists: {path}")
        else:
            new_idep = None
        return SupplyInfo(file, available, deferred, new_idep)

    def create_file(
        self, creator: Node, path: str, file_state: FileState
    ) -> tuple[Node, list[Node]]:
        """Create (or recycle) a file with a STATIC, PENDING or VOLATILE file state.

        Parameters
        ----------
        creator
            The creating step or static root.
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
            raise ValueError("Cannot create a STATIC file. It must be MISSING first.")
        if file_state == FileState.VOLATILE and path.endswith(os.sep):
            raise GraphError("A volatile output cannot be a directory.")
        if not (creator.kind() == "sr" or self.matching_static_root(path) is None):
            raise GraphError("Cannot manually add a file that matches a static root.")

        file = self.create(File, creator, path, state=file_state)

        if file_state == FileState.VOLATILE:
            # Do not allow volatile files to have consumers.
            if any(file.consumers()):
                raise GraphError(f"An input to an existing step cannot be volatile: {path}")
        else:
            # Watch parent directories of non-volatile files.
            self.put_dir_queue(Path(path).parent)
        return file

    def put_dir_queue(self, path: Path | str):
        """Put a directory in the dir_queue, with some consistency checks."""
        path = Path(path)
        if path == "":
            path = Path(".")
        if self.makedirs:
            path.makedirs_p()
        if self.dir_queue is not None:
            self.dir_queue.put_nowait(path)

    def declare_missing(self, creator: Node, paths=Collection[str]) -> list[tuple[str, FileHash]]:
        """Declare a files as missing, with the intention to later confirm them as static.

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
        self._check_node(creator, "creator", allow_root=True)
        # Sort paths to make the operation deterministic.
        paths = sorted(set(paths))
        # Define the files and create a list of (path, file_hash) tuples.
        missing = [self.create_file(creator, path, FileState.MISSING) for path in paths]
        # Collect a list of paths and file hashes to be checked.
        return self._build_to_check(missing)

    def _build_to_check(self, deferred: Collection[Node]) -> list[tuple[str, FileHash]]:
        """Convert a list of MISSING file nodes to a list of (path, file_hash) tuples.

        This list is intended to be returned to the caller, so the validity of the files
        can be checked and confirmed as static in a follow-up RPC call.

        Parameters
        ----------
        deferred
            MISSING file nodes that match a static root.

        Returns
        -------
        to_check
            A list of paths and file_hashes.
            These must be sent back to the client where the hashes can be checked
            and which then calls `confirm_hashes` with the updated hashes.
        """
        self.con.execute("DROP TABLE IF EXISTS temp.missing")
        try:
            self.con.execute("CREATE TABLE temp.missing(node INTEGER PRIMARY KEY)")
            sql = "INSERT INTO temp.missing VALUES (?)"
            self.con.executemany(sql, ((file.i,) for file in deferred))
            sql = (
                "SELECT label, digest, mode, mtime, size, inode "
                "FROM temp.missing "
                "JOIN node ON node.i = temp.missing.node "
                "JOIN file ON file.node = temp.missing.node"
            )
            return [
                (path, FileHash(digest, mode, mtime, size, inode))
                for path, digest, mode, mtime, size, inode in self.con.execute(sql)
            ]
        finally:
            self.con.execute("DROP TABLE IF EXISTS temp.missing")

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
        self.con.execute("DROP TABLE IF EXISTS temp.paths")
        try:
            self.con.execute("CREATE TABLE temp.paths(path TEXT PRIMARY KEY)")
            self.con.executemany("INSERT INTO temp.paths VALUES (?)", ((path,) for path in paths))
            sql = (
                "SELECT label, digest, mode, mtime, size, inode FROM node "
                "JOIN file ON file.node = node.i JOIN temp.paths ON label = temp.paths.path"
            )
            return [
                (path, FileHash(digest, mode, mtime, size, inode))
                for path, digest, mode, mtime, size, inode in self.con.execute(sql)
            ]
        finally:
            self.con.execute("DROP TABLE IF EXISTS temp.paths")

    def update_file_hashes(self, file_hashes: Collection[tuple[str, FileHash]], cause: str):
        """Update the hashes of existing files.

        Parameters
        ----------
        file_hashes
            A list of `(path, file_hash)` tuples.
        cause
            The cause of the hash updates.
            Can be one of the following:
            `"external"`, `"succeeded"`, `"failed"` or `"confirmed"`.
        """
        if cause not in ("external", "succeeded", "failed", "confirmed"):
            raise ValueError("Invalid cause for updating file hashes.")
        if len(file_hashes) == 0:
            return

        # Efficiently get corresponding node_index and state tuples.
        file_hashes = dict(file_hashes)
        self.con.execute("DROP TABLE IF EXISTS temp.updated")
        try:
            self.con.execute("CREATE TABLE temp.updated(path TEXT PRIMARY KEY) WITHOUT ROWID")
            self.con.executemany(
                "INSERT INTO temp.updated VALUES (?)",
                ((path,) for path in file_hashes),
            )
            sql = (
                "SELECT node.i, path, file.state FROM temp.updated "
                "JOIN node ON node.label = path JOIN file ON file.node = node.i "
            )
            records = [
                (i, path, file_hashes[path], FileState(value))
                for i, path, value in self.con.execute(sql)
            ]
        finally:
            self.con.execute("DROP TABLE IF EXISTS temp.updated")

        if len(records) != len(file_hashes):
            raise AssertionError(
                f"Inconsistent number of records: expected={len(file_hashes)} actual={len(records)}"
            )

        # Files whose `externally_updated` method must be called.
        updated = []
        # Files whose `externally_deleted` method must be called.
        deleted = []
        # File whose `release` method should be called
        completed = []
        # Files whose state and hash must be updated.
        new_states_hashes = []

        def raise_unexpected(path, old_state, fh):
            raise AssertionError(
                f"Unexpected file hash update: cause={cause} path={path} state={old_state} "
                f"digest={fmt_digest(fh.digest)} mode={stat.filemode(fh.mode)}"
            )

        # Decide how the file state must change and which other actions to take on the files
        # based on the cause of the hash updates.
        if cause == "external":
            # This is branch is relevant for the end of the watch phase or the startup of StepUp
            for i, path, new_fh, old_state in records:
                if old_state == FileState.MISSING:
                    if new_fh.is_unknown:
                        raise AssertionError(f"Missing updated to be missing again: {path}")
                    new_states_hashes.append((i, FileState.STATIC, new_fh))
                    updated.append((i, path))
                elif old_state == FileState.STATIC:
                    if new_fh.is_unknown:
                        new_states_hashes.append((i, FileState.MISSING, new_fh))
                        deleted.append((i, path))
                    else:
                        new_states_hashes.append((i, FileState.STATIC, new_fh))
                        updated.append((i, path))
                elif old_state in (FileState.BUILT, FileState.OUTDATED):
                    new_states_hashes.append((i, FileState.AWAITED, FileHash.unknown()))
                    if new_fh.is_unknown:
                        deleted.append((i, path))
                    else:
                        updated.append((i, path))
                else:
                    raise_unexpected(path, old_state, new_fh)
        elif cause == "succeeded":
            # This branch is relevant for when a step has succeeded
            # and its outputs should be marked as BUILT.
            for i, path, new_fh, old_state in records:
                if old_state in (FileState.OUTDATED, FileState.AWAITED):
                    if new_fh.is_unknown:
                        raise AssertionError(f"Unknown file hash after succeeded step: {path}")
                    new_states_hashes.append((i, FileState.BUILT, new_fh))
                    completed.append((i, path))
                else:
                    raise_unexpected(path, old_state, new_fh)
        elif cause == "failed":
            # This branch is relevant for when a step has failed or rescheduled
            # and its outputs should be marked as OUTDATED.
            for i, path, new_fh, old_state in records:
                if old_state in (FileState.OUTDATED, FileState.AWAITED):
                    new_states_hashes.append(
                        (i, FileState.AWAITED if new_fh.is_unknown else FileState.OUTDATED, new_fh)
                    )
                else:
                    raise_unexpected(path, old_state, new_fh)
        elif cause == "confirmed":
            # This branch is relevant for when the client has confirmed the hashes
            # of missing files and they should be marked as STATIC.
            for i, path, new_fh, old_state in records:
                if old_state == FileState.MISSING:
                    if new_fh.is_unknown:
                        raise AssertionError(f"Missing file confirmed as missing: {path}")
                    new_states_hashes.append((i, FileState.STATIC, new_fh))
                    completed.append((i, path))
                else:
                    raise_unexpected(path, old_state, new_fh)
        else:
            raise ValueError(f"Invalid cause for updating file hashes: {cause}")

        # Actual update of the file hashes.
        logger.info("Update file hashes: cause=%s new=%s", cause, new_states_hashes)
        if len(new_states_hashes) != len(file_hashes):
            raise AssertionError(
                f"Inconsistent number of file hash updates: "
                f"expected={len(file_hashes)} actual={len(new_states_hashes)}"
            )
        self.con.executemany(
            "UPDATE file SET state = ?, digest = ?, mode = ?, mtime = ?, size = ?, inode = ? "
            "WHERE node = ?",
            (
                (state.value, fh.digest, fh.mode, fh.mtime, fh.size, UInt64(fh.inode), i)
                for i, state, fh in new_states_hashes
            ),
        )

        # Call File methods to further update the workflow.
        logger.info(
            "Update file hashes: cause=%s updated=%s deleted=%s completed=%s",
            cause,
            updated,
            deleted,
            completed,
        )
        for i, path in updated:
            File(self, i, path).externally_updated()
        for i, path in deleted:
            File(self, i, path).externally_deleted()
        for i, path in completed:
            File(self, i, path).completed()

    def define_step(
        self,
        creator: Node,
        command: str,
        *,
        inp_paths: Collection[str] = (),
        env_vars: Collection[str] = (),
        out_paths: Collection[str] = (),
        vol_paths: Collection[str] = (),
        workdir: str = "./",
        need: Need = Need.DEFAULT,
        resources: dict[str, int] | None = None,
        safe: bool = False,
        subshell: bool = False,
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
        env_vars
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
        self._check_node(creator, "creator", allow_root=True)

        # Sanity check
        if not workdir.endswith(os.sep):
            raise GraphError("The working directory must end with a trailing separator")

        # If it is a boot step, check that there was no boot step yet.
        if creator.i == self.root.i and any(self.root.products(Step)):
            raise GraphError("Boot step already defined.")

        # Normalize arguments
        inp_paths = sorted(set(inp_paths))
        env_vars = sorted(set(env_vars))
        out_paths = sorted(set(out_paths))
        vol_paths = sorted(set(vol_paths))
        if any(inp_path.endswith(os.sep) for inp_path in inp_paths):
            raise GraphError("Directory inputs are not supported.")

        # If a matching detached step is found, reuse it, instead of creating a new one.
        old_deferred = self._recreate_step(
            command,
            workdir,
            inp_paths,
            env_vars,
            out_paths,
            vol_paths,
            need,
            resources,
            creator,
            subshell,
        )
        if old_deferred is not None:
            return self._build_to_check(old_deferred)

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

        # Keep track of all missing files that match a static root and need to be confirmed.
        deferred = set()

        # Supply inp_paths
        for inp_path in inp_paths:
            # We do not care about the unavailable files here,
            # because the step will only be executed when all inputs are available.
            deferred.update(self.supply_file(step, inp_path).deferred)

        # Process vars
        step.add_env_vars(env_vars)

        # Create out_paths
        for out_path in out_paths:
            file = self.create_file(step, out_path, FileState.AWAITED)
            file.add_supplier(step)

        # Create vol_paths
        for vol_path in vol_paths:
            file = self.create_file(step, vol_path, FileState.VOLATILE)
            file.add_supplier(step)

        # Determine if the step needs executing and queue if relevant.
        logger.info("Define step: %s", step.label)
        return self._build_to_check(deferred)

    def _recreate_step(
        self,
        command: str,
        workdir: str,
        inp_paths: list[str],
        env_vars: list[str],
        out_paths: list[str],
        vol_paths: list[str],
        need: Need,
        resources: dict[str, int] | None,
        creator: Node,
        subshell: bool = False,
    ) -> set[Node] | None:
        """Recreate a step if it was detached and the step arguments are compatible.

        Returns
        -------
        deferred
            If the step can be reused, a possibly empty list is returned with
            MISSING file nodes that match a static root and need to be confirmed.
        """
        label = Step.create_label(command, workdir=workdir)
        old_step, detached = self.find_detached(Step, label)

        # Check whether the step can be reused.
        if old_step is None or not detached:
            return None
        old_inp_paths = sorted(
            item[0] for item in old_step.inp_paths(amended=False, yield_detached=True)
        )
        if old_inp_paths != inp_paths:
            return None
        old_env_vars = sorted(old_step.env_vars(amended=False))
        if old_env_vars != env_vars:
            return None
        old_out_paths = sorted(
            item[0] for item in old_step.out_paths(amended=False, yield_detached=True)
        )
        if old_out_paths != out_paths:
            return None
        old_vol_paths = sorted(
            item[0] for item in old_step.vol_paths(amended=False, yield_detached=True)
        )
        if old_vol_paths != vol_paths:
            return None

        # We have a match!

        # Update the need, subshell values and _check_* flags.
        self.con.execute(
            "UPDATE step SET need = ?, subshell = ?, _check_safe = 1, _check_after = 1 "
            "WHERE node = ?",
            (need.value, int(subshell), old_step.i),
        )

        # Restore the step and its products (recursively), and set resources.
        old_step.recycle(creator)
        old_step.set_resources(resources)

        # If inputs of the recreated steps are AWAITED or OUTDATED, these steps must be rescheduled.
        for i, label in self.con.execute(RECURSE_OUTDATED_STEPS, (old_step.i,)):
            step = Step(self, i, label)
            step.mark_pending()

        # Look for MISSING inputs and determine which were matching a static root.
        # Their existence still needs to be checked by the client and ideally confirmed as existing
        # in a follow-up call to `confirm_hashes`.
        deferred = {
            File(self, i, label)
            for i, label in self.con.execute(RECURSE_DEFERRED_INPUTS, (old_step.i,))
        }

        logger.info("Reuse detached step: %s", old_step.label)
        return deferred

    def amend_step(
        self,
        step: Step,
        *,
        inp_paths: Collection[str] = (),
        env_vars: Collection[str] = (),
        out_paths: Collection[str] = (),
        vol_paths: Collection[str] = (),
    ) -> tuple[bool, list[tuple[File, FileState]]]:
        """Amend step information.

        Parameters
        ----------
        step
            The step specifying the additional info.
        inp_paths
            Additional input paths.
        env_vars
            Additional environment variables that the step is using.
        out_paths
            Additional output paths.
        vol_paths
            Volatile output (not reproducible) but will be cleaned like built files.

        Returns
        -------
        keep_going
            True when known inputs are readily available.
            False otherwise, meaning the step needs to be rescheduled.
        to_check
            A list of paths and file_hashes.
            These must be sent back to the client where the hashes can be checked
            and which then calls `confirm_hashes` with the updated hashes.
            (This is only relevant when keep_going is True.
            If some to_check files turn out to be missing, keep_going should be changed to False.)
        """
        self._check_node(step, "step")

        # Normalize arguments
        inp_paths = sorted(set(inp_paths))
        out_paths = sorted(set(out_paths))
        vol_paths = sorted(set(vol_paths))
        if any(inp_path.endswith(os.sep) for inp_path in inp_paths):
            raise GraphError("Directory inputs are not supported.")

        # Keep track of missing files, of which there are two different types:
        # - unavailable = certainly not available
        # - deferred = possibly available but need to be checked.
        #   For example, these can be MISSING files that need to be confirmed as STATIC.
        unavailable = set()
        deferred = set()

        # Process inp_paths
        for inp_path in inp_paths:
            info = self.supply_file(step, inp_path, new=False)
            if not info.available:
                unavailable.add(inp_path)
            if info.new_idep is not None:
                self._amend_dep(info.new_idep)
            deferred.update(info.deferred)

        # Process vars
        step.amend_env_vars(env_vars)

        # Create out_paths
        for out_path in out_paths:
            file = self.create_file(step, out_path, FileState.AWAITED)
            new_idep = file.add_supplier(step)
            self._amend_dep(new_idep)

        # Create vol_paths
        for vol_path in vol_paths:
            file = self.create_file(step, vol_path, FileState.VOLATILE)
            new_idep = file.add_supplier(step)
            self._amend_dep(new_idep)

        if len(unavailable) > 0:
            step.add_rescheduled_info("\n".join(sorted(unavailable)))

        return len(unavailable) == 0, self._build_to_check(deferred)

    def _amend_dep(self, idep):
        self.con.execute("INSERT INTO amended_dep VALUES (?)", (idep,))

    def register_nglob(self, step: Step, nglob_multi: NGlobMulti):
        self._check_node(step, "step")
        step.register_nglob(nglob_multi)

    def register_static_root(self, creator: Node, path: str) -> list[tuple[str, FileHash]]:
        """Install a static root.

        Parameters
        ----------
        creator
            The step creating the static root.
        path
            A path to a directory that will be treated as a static root.

        Returns
        -------
        to_check
            A list of matching (path, file_hash) whose existence and validity must be checked.
            The client must call `confirm_hashes` after checking files with resulting hashes.
        """
        self._check_node(creator, "creator")
        if not isinstance(path, str):
            raise TypeError("The argument path must be a string.")
        if path.startswith("/"):
            raise ValueError(f"Static root paths cannot be absolute paths: {path}")
        if has_wildcards(path):
            raise ValueError(f"Static root does not support wildcards: {path}")
        if not path.endswith(os.sep):
            path = path + os.sep
        if self.matching_static_root(path) is not None:
            raise GraphError(f"Static root is a subdirectory of an existing static root: {path}")
        sql = "SELECT 1 FROM node WHERE kind = 'sr' AND NOT detached AND label LIKE ? ESCAPE '\\'"
        pattern = f"{escape_like_pattern(path)}%"
        if self.con.execute(sql, (pattern,)).fetchone() is not None:
            raise GraphError(
                f"Static root is a parent directory of an existing static root: {path}"
            )
        sr = self.create(StaticRoot, creator, path)
        # Check for matches in existing files.
        # For example previously defined inputs whose origin was not determined yet.
        pattern = f"{escape_like_pattern(path)}%"
        sql = (
            "SELECT label FROM node JOIN file ON node.i = file.node "
            f"WHERE state != {FileState.MISSING.value} "
            "AND node.label LIKE ?"
        )
        matching_paths = [path for (path,) in self._con.execute(sql, (pattern,))]
        return self.declare_missing(sr, matching_paths)

    def clean(self):
        # Get rid of static root files that are no longer used.
        for sr in self.nodes(StaticRoot):
            files = sorted(sr.products(), reverse=True, key=(lambda node: node.path))
            for file in files:
                if not any(file.consumers()):
                    file.detach()
        super().clean()

    #
    # Watch phase
    #

    def is_relevant(self, path: str) -> bool:
        file, detached = self.find_detached(File, path)
        if not (file is None or detached):
            return file.get_state() not in (FileState.AWAITED, FileState.VOLATILE)
        return any(ngm.may_change(set(), {path}) for ngm in self.nglob_multis())

    def iter_relevant(self, parent: str) -> Iterator[str]:
        """Iterate over all non-detached files that are relevant for a given parent directory."""
        sql = (
            "SELECT label FROM node JOIN file ON node.i = file.node "
            f"WHERE state NOT IN ({FileState.AWAITED.value}, {FileState.VOLATILE.value}) AND "
            "node.label LIKE ? AND NOT detached"
        )
        pattern = f"{escape_like_pattern(parent)}%"
        for (path,) in self.con.execute(sql, (pattern,)):
            yield path

    def nglob_multis(self, yield_step: bool = False) -> Iterator[NGlobMulti]:
        sql = (
            "SELECT node.i, label, kind, nglob_multi.i, data "
            "FROM node JOIN nglob_multi ON node.i = nglob_multi.node"
        )
        for node_i, label, kind, ngm_i, data in self._con.execute(sql):
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
                self.con.execute("UPDATE nglob_multi SET data = ? WHERE i = ?", data)
                step.mark_pending()
