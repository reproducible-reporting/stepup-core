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
"""The `Workflow` is a `Cascade` subclass with more concrete node implementations."""

import asyncio
import json
import logging
import os
import pickle
import re
import shlex
import textwrap
from collections.abc import Collection, Iterator

import attrs

from .cascade import Cascade, Node
from .deferred_glob import DeferredGlob
from .enums import FileState, Mandatory, StepState
from .exceptions import GraphError
from .file import File
from .hash import FileHash
from .job import SetPoolJob
from .nglob import NGlobMulti, convert_nglob_to_regex, iter_wildcard_names
from .step import Step
from .utils import myparent

__all__ = ("Workflow",)


logger = logging.getLogger(__name__)


@attrs.define(eq=False)
class Workflow(Cascade):
    # Steps ready for scheduling and execution.
    job_queue: asyncio.queues = attrs.field(init=False, factory=asyncio.Queue)

    # Directories to be (un)watched
    dir_queue: asyncio.queues = attrs.field(init=False, factory=asyncio.Queue)

    # This event is set when the scheduler should check the job_queue again.
    job_queue_changed: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    # A list of files and directories that can be deleted.
    # This list contains BUILT files node with file hashes that were removed from the graph.
    to_be_deleted: list[tuple[str, FileHash | None]] = attrs.field(init=False, factory=list)

    #
    # Initialization
    #

    def check_consistency(self):
        """Check whether the initial graph satisfies all constraints."""
        super().check_consistency()
        for node in self.nodes():
            if node.kind() == "file":
                parent = self.get_parent(node.label, allow_orphan=True)
                if parent is not None:
                    sql = "SELECT 1 FROM dependency WHERE supplier = ? AND consumer = ?"
                    row = self._con.execute(sql, (parent.i, node.i)).fetchone()
                    if row is None:
                        raise ValueError(f"File is not consumer of its parent: {node.label}")

    def initialize_boot(self, path_plan: str) -> bool:
        """Initialize the (new) boot script.

        Returns
        -------
        initialized
            Whether the boot script was (re)initialized.
        """
        if os.sep in path_plan:
            raise ValueError(
                "The initial plan.py cannot contain directory separators and "
                "must be in the current directory."
            )
        command = shlex.quote(f"./{path_plan}")
        nodes = {node.key(): node for node in self.root.products()}
        del nodes["root:"]
        if (
            len(nodes) >= 3
            and "file:./" in nodes
            and nodes["file:./"].get_state() == FileState.STATIC
            and f"file:{path_plan}" in nodes
            and nodes[f"file:{path_plan}"].get_state() == FileState.STATIC
            and f"step:{command}" in nodes
        ):
            # The boot steps are already present (from a previous invocation of stepup).
            return False

        # Need to (re)initialize the boot steps.
        for node in nodes.values():
            node.orphan()
        to_check = self.declare_missing(self.root, ["./", path_plan])
        for path, file_hash in to_check:
            file_hash.update(path)
        self.confirm_static(to_check)
        self.define_step(self.root, command, inp_paths=[path_plan])
        return True

    @staticmethod
    def default_node_classes() -> list[type[Node]]:
        return [*Cascade.default_node_classes(), File, Step, DeferredGlob]

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
            elif kind == "dg":
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
            "SELECT i, kind, label FROM node "
            "WHERE NOT (kind = 'file' AND label LIKE '%/') AND NOT (kind = 'root')",
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
            "WHERE NOT node.orphan GROUP BY file.state"
        )
        return {FileState(value): count for value, count in self.con.execute(sql)}

    def get_step_counts(self) -> dict[StepState, int]:
        """Return counters for StepState."""
        sql = (
            "SELECT step.state, count(*) FROM node JOIN step ON node.i = step.node "
            "WHERE NOT node.orphan AND step.mandatory != ? GROUP BY step.state"
        )
        data = (Mandatory.NO.value,)
        return {StepState(value): count for value, count in self.con.execute(sql, data)}

    def steps(self, state: StepState) -> Iterator[Step]:
        sql = (
            "SELECT i, label FROM node JOIN step ON node.i = step.node "
            "WHERE state = ? AND NOT orphan"
        )
        for i, label in self.con.execute(sql, (state.value,)):
            yield Step(self, i, label)

    def orphaned_inp_paths(self) -> Iterator[str, FileState]:
        """Iterate over input paths that are used by steps by have not been created yet."""
        sql = (
            "SELECT node.label, file.state FROM node JOIN file ON node.i = file.node "
            "WHERE node.orphan = TRUE "
            "AND EXISTS (SELECT 1 FROM dependency WHERE supplier = file.node)"
        )
        for row in self.con.execute(sql):
            yield row[0], FileState(row[1])

    def missing_paths(self) -> Iterator[str]:
        """Iterate over static files that have been deleted or were never confirmed."""
        sql = (
            "SELECT label FROM node JOIN file ON node.i = file.node "
            "WHERE state = ? AND NOT orphan"
            ""
        )
        for row in self.con.execute(sql, (FileState.MISSING.value,)):
            yield row[0]

    #
    # Run phase
    #

    def _check_node(self, node: Node, argname: str, allow_root=False):
        """Check the creator or step key in the methods below."""
        if not isinstance(node, Node):
            raise TypeError(f"{argname} must be a Node instance, got: {type(node)}")
        if allow_root and node.i == self.root.i:
            return
        if node.kind() not in ("step", "dg"):
            allowed = "step, dg or root" if allow_root else "step or dg"
            raise ValueError(f"{argname} is not a {allowed}: '{node.key()}'")
        if node.is_orphan():
            raise ValueError(f"{argname} is orphan: '{node.key()}'")

    def supply_parent(self, file: File) -> tuple[Node | None, bool, bool]:
        """Create or find a parent directory file node.

        Parameters
        ----------
        file
            The file object for which a parent must be provided.

        Returns
        -------
        parent
            A parent file object
        available
            True when the requested parent is BUILT or STATIC.
        new_relation
            True when the requested relation is new.
        """
        parent_path = myparent(file.path)
        if parent_path is None:
            return None, True, False
        return self.supply_file(file, parent_path, new=False)

    def matching_deferred_glob(self, path: str) -> DeferredGlob | None:
        dgs = []
        sql = "SELECT i, label FROM node WHERE kind = 'dg' AND NOT orphan"
        for i, label in self.con.execute(sql):
            if re.fullmatch(convert_nglob_to_regex(label), path) is not None:
                dgs.append(DeferredGlob(self, i, label))
        if len(dgs) > 1:
            raise GraphError(f"Multiple deferred globs match: {path}")
        if len(dgs) == 1:
            return dgs[0]
        return None

    @staticmethod
    def always_static(path: str) -> bool:
        return path in ["./", "/"] or path == "../" * (len(path) // 3)

    def supply_file(self, node: Node, path: str, new: bool = True) -> tuple[Node, bool, bool]:
        """Find an existing file or create on orphan file, and make it a supplier of node.

        Parameters
        ----------
        node
            The node to supply to.
        path
            The file that should supply to the step.
        new
            When True the (file, step) relationship must be new.
            If not, a ValueError is raised.

        Returns
        -------
        file
            The key of the created / recycled file.
        available
            True when the requested path is BUILT or STATIC.
        new_idep
            dependency identifier when the relation is new.
            None otherwise.
        """
        available = False
        file, is_orphan = self.find("file", path, return_orphan=True)
        if file is None or is_orphan:
            if self.always_static(path):
                file = self.create("file", self.root, path, state=FileState.MISSING)
                file_hash = FileHash.unknown()
                file_hash.update(path)
                self.confirm_static([(path, file_hash)])
            else:
                file = self.create("file", None, path, state=FileState.AWAITED)
            self.supply_parent(file)
        else:
            state = file.get_state()
            if state == FileState.VOLATILE:
                raise GraphError(f"Input is volatile: {path}")
            available = state in (FileState.BUILT, FileState.STATIC)
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
        return file, available, new_idep

    def create_file(self, creator: Node, path: str, file_state: FileState) -> Node:
        """Create (or recycle) a file with a STATIC, PENDING or VOLATILE file state.

        Parameters
        ----------
        creator
            The creating step or deferred glob.
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
        if file_state == FileState.BUILT:
            raise ValueError("Cannot create a BUILT file. It must be AWAITED first.")
        if file_state == FileState.VOLATILE and path.endswith(os.sep):
            raise GraphError("A volatile output cannot be a directory.")
        if not (creator.kind() == "dg" or self.matching_deferred_glob(path) is None):
            raise GraphError("Cannot manually add a file that matches a deferred glob.")
        if file_state != FileState.STATIC and self.always_static(path):
            raise GraphError(f"Path is created as {file_state} but must be static: {path}")
        file = self.create("file", creator, path, state=file_state)
        if file_state == FileState.VOLATILE and any(file.consumers()):
            raise GraphError(f"An input to an existing step cannot be volatile: {path}")
        parent, _, _ = self.supply_parent(file)
        parent_state = None if parent is None else parent.get_state()
        if file_state == FileState.STATIC:
            if not (parent_state is None or parent_state == FileState.STATIC):
                raise GraphError(f"Static path does not have a static parent path node: {path}")
            file.release_pending()
        elif file_state == FileState.MISSING:
            if not (parent_state is None or parent_state in (FileState.STATIC, FileState.MISSING)):
                raise GraphError(
                    f"Missing path does not have a static or missing parent path node: {path}"
                )
        return file

    def get_parent(self, path: str, allow_orphan: bool = False) -> Node | None:
        """Get the a parent File object, if relevant for consistency checking.

        Parameters
        ----------
        path
            The path whose parent is requested.
        allow_orphan
            Set to True if an orphan parent path node is acceptable.

        Raises
        ------
        ValueError
            When the path or parent path are non-trivial and the parent path key could not be found.

        Returns
        -------
        parent
            This is None when the path or parent path are always static.
            In all other cases, the parent file object is returned.
        """
        if self.always_static(path):
            return None
        parent_path = myparent(path)
        if self.always_static(parent_path):
            return None
        parent, is_orphan = self.find("file", parent_path, return_orphan=True)
        if parent is None or (is_orphan and not allow_orphan):
            raise ValueError(f"Path does not have a parent path node: {path}")
        return parent

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
            and which then calls `confirm_static` with the updated hashes.
        """
        if isinstance(paths, str):
            raise TypeError("The paths argument cannot be a string.")
        self._check_node(creator, "creator", allow_root=True)
        # Sort paths to get parent directories before files containing them.
        paths = sorted(set(paths))
        # Define the files and create a list of (path, file_hash) tuples.
        files = [
            self.create_file(creator, path, FileState.MISSING)
            for path in paths
            if not self.always_static(path)
        ]
        # Collect a list of paths and file hashes to be checked.
        self.con.execute("DROP TABLE IF EXISTS temp.missing")
        try:
            self.con.execute("CREATE TABLE temp.missing(node INTEGER PRIMARY KEY)")
            sql = "INSERT INTO temp.missing VALUES (?)"
            self.con.executemany(sql, ((file.i,) for file in files))
            to_check = []
            sql = (
                "SELECT label, digest, mtime, mode, size, inode FROM temp.missing "
                "JOIN node ON node.i = temp.missing.node "
                "JOIN file ON file.node = temp.missing.node"
            )
            for path, digest, mtime, mode, size, inode in self.con.execute(sql):
                to_check.append((path, FileHash(digest, mtime, mode, size, inode)))
        finally:
            self.con.execute("DROP TABLE IF EXISTS temp.missing")
        return to_check

    def filter_deferred(self, paths: Collection[str]) -> list[tuple[str, FileHash]]:
        """Add all paths that match a deferred glob as missing, to be comfirned as static later.

        Parameters
        ----------
        paths
            The list of paths to test against available deferred globs in the graph.

        Returns
        -------
        to_check
            A list of paths and file_hashes.
            These must be sent back to the client where the hashes can be checked
            and which then calls `confirm_static` with the updated hashes.
        """
        matching = {}
        for path in paths:
            # All parents of the path must be checked.
            all_paths = []
            while path is not None:
                file, is_orphan = self.find("file", path, return_orphan=True)
                if not (file is None or is_orphan):
                    break
                all_paths.insert(0, path)
                path = myparent(path)
            for path_up in all_paths:
                dg = self.matching_deferred_glob(path_up)
                if dg is not None:
                    matching.setdefault(dg, []).append(path_up)
        to_check = []
        for dg, matching_paths in matching.items():
            to_check.extend(self.declare_missing(dg, matching_paths))
        return to_check

    def confirm_static(self, checked: Collection[tuple[str, FileHash]]):
        """Make missing files static with updated hashes.

        Parameters
        ----------
        checked
            A list of (path, file_hash) tuples computed on the client side,
            of which the file hashes must confirm that the files exist.
        """
        for path, file_hash in checked:
            if file_hash.digest == b"u":
                raise AssertionError(f"Missing static file: {path}")

        files = self.update_file_hashes(checked, return_files=True)
        for file, file_state in files:
            if file_state != FileState.MISSING:
                raise AssertionError(f"File was not missing: {file.path}")
            file.set_state(FileState.STATIC)
            file.release_pending()

    def update_file_hashes(
        self, file_hashes: Collection[tuple[str, FileHash]], return_files: bool = False
    ) -> list[tuple[File, FileState]] | None:
        """Update the hashes of existing files.

        Parameters
        ----------
        file_hashes
            A list of (path, file_hash) tuples.
        return_files
            If True, return a list of (file, file_state) tuples.
            (No return value otherwise)

        Returns
        -------
        files
            A list of (file, file_state) tuples.
        """
        # Load the hashes into a temporary table.
        self.con.execute("DROP TABLE IF EXISTS temp.checked")
        try:
            self.con.execute(
                "CREATE TABLE temp.checked(path TEXT PRIMARY KEY, node INTEGER, digest BLOB, "
                "mtime INTEGER, mode INTEGER, size INTEGER, inode INTEGER)"
            )
            self.con.executemany(
                "INSERT INTO temp.checked VALUES (?, 0, ?, ?, ?, ?, ?)",
                (
                    (path, fh.digest, fh.mtime, fh.mode, fh.size, fh.inode)
                    for path, fh in file_hashes
                ),
            )
            self.con.execute(
                "UPDATE temp.checked SET node = node.i FROM node WHERE node.label = path"
            )

            # Get the files that need to be confirmed and check that they are indeed missing.
            result = None
            if return_files:
                result = [
                    (File(self, i, label), FileState(file_state_value))
                    for i, label, file_state_value in self.con.execute(
                        "SELECT temp.checked.node, temp.checked.path, file.state FROM temp.checked "
                        "JOIN file ON file.node = temp.checked.node"
                    )
                ]

            # Actual update of the file hashes.
            cur = self.con.execute(
                "UPDATE file SET digest = temp.checked.digest, mtime = temp.checked.mtime, "
                "mode = temp.checked.mode, size = temp.checked.size, inode = temp.checked.inode "
                "FROM temp.checked WHERE file.node = temp.checked.node"
            )
            if cur.rowcount != len(file_hashes):
                raise ValueError("Not all file hashes could be updated.")
        finally:
            # Clean up
            self.con.execute("DROP TABLE IF EXISTS temp.checked")

        return result

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
        optional: bool = False,
        pool: str | None = None,
        block: bool = False,
    ) -> Node:
        """Define a new step.

        Parameters
        ----------
        creator
            The step that generated this step.
            This is None for the boot script.
        command
            A command that can be executed by /usr/bin/sh.
        inp_paths
            Input paths.
        env_vars
            The environment variables used by the step.
        out_paths
            Output paths.
        vol_paths
            Volatile output (not reproducible) but will be cleaned like built files.
        workdir
            The directory where the command should be executed,
            typically relative to the working directory of the director.
        optional
            If True, the step is only executed when required by other mandatory steps.
        pool
            The pool in which to execute this step, if any.
        block
            Block the step from being executed, convenient for temporarily reducing the workflow
            without cleaning up results of blocked steps.

        Returns
        -------
        step
            The newly created step (or existing if it was previously orphaned).
        """
        self._check_node(creator, "creator", allow_root=True)

        # Sanity check
        if not workdir.endswith(os.sep):
            raise GraphError("The working directory must end with a trailing separator")

        # If it is a boot step, check that there was no boot step yet.
        if creator.i == self.root.i and any(self.root.products(kind="step")):
            raise GraphError("Boot step already defined.")

        # Normalize arguments
        inp_paths = sorted(set(inp_paths))
        env_vars = sorted(set(env_vars))
        out_paths = sorted(set(out_paths))
        vol_paths = sorted(set(vol_paths))

        # Deduce required directories
        reqdirs = {workdir}
        reqdirs.update(myparent(out_path) for out_path in out_paths)
        reqdirs.update(myparent(vol_path) for vol_path in vol_paths)
        reqdirs.difference_update(inp_paths)
        reqdirs.difference_update(out_paths)
        reqdirs.difference_update(vol_paths)
        reqdirs = sorted(reqdirs)

        # If a matching orphaned step is found, reuse it, instead of creating a new one.
        old_step = self._recreate_step(
            command,
            workdir,
            inp_paths,
            reqdirs,
            env_vars,
            out_paths,
            vol_paths,
            optional,
            pool,
            block,
            creator,
        )
        if old_step is not None:
            return old_step

        # Create new step
        step = self.create(
            "step",
            creator,
            command=command,
            workdir=workdir,
            pool=pool,
            block=block,
            mandatory=Mandatory.NO if optional else Mandatory.YES,
        )

        # Supply required directories
        for reqdir in sorted(reqdirs):
            self.supply_file(step, reqdir, new=False)

        # Supply inp_paths
        for inp_path in inp_paths:
            # Input coinciding with reqdirs are ignored.
            if inp_path not in reqdirs:
                self.supply_file(step, inp_path)

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
        if not optional:
            step.queue_if_appropriate()

        logger.info("Define step: %s", step.label)
        return step

    def _recreate_step(
        self,
        command: str,
        workdir: str,
        inp_paths: list[str],
        reqdirs: list[str],
        env_vars: list[str],
        out_paths: list[str],
        vol_paths: list[str],
        optional: bool,
        pool: str | None,
        block: bool,
        creator: Node,
    ) -> Node | None:
        """Recreate a step if it was orphaned and the step arguments are compatible."""
        label = Step.create_label("", command, workdir)
        old_step, is_orphan = self.find("step", label, return_orphan=True)

        # Check whether the step can be reused.
        if old_step is None or not is_orphan:
            return None
        old_inp_paths = sorted(
            item[0] for item in old_step.inp_paths(amended=False, yield_orphan=True)
        )
        if old_inp_paths != sorted({*inp_paths, *reqdirs}):
            return None
        old_env_vars = sorted(old_step.env_vars(amended=False))
        if old_env_vars != env_vars:
            return None
        old_out_paths = sorted(
            item[0] for item in old_step.out_paths(amended=False, yield_orphan=True)
        )
        if old_out_paths != out_paths:
            return None
        old_vol_paths = sorted(
            item[0] for item in old_step.vol_paths(amended=False, yield_orphan=True)
        )
        if old_vol_paths != vol_paths:
            return None

        # We have a match, restore the step and update the pool and block settings.
        old_step.recreate(creator)
        old_step.set_mandatory(Mandatory.NO if optional else Mandatory.YES)
        old_step.check_imply_mandatory()
        self.con.execute(
            "UPDATE step SET pool = ?, block = ? WHERE node = ?", (pool, block, old_step.i)
        )
        old_step.queue_if_appropriate()
        logger.info("Reuse orphaned step: %s", old_step.label)
        return old_step

    def define_pool(self, step: Step, pool: str, size: int):
        """Set the pool size and keep the information in the step to support replay."""
        step.define_pool(pool, size)
        self.job_queue.put_nowait(SetPoolJob(pool, size))
        self.job_queue_changed.set()

    def amend_step(
        self,
        step: Step,
        *,
        inp_paths: Collection[str] = (),
        env_vars: Collection[str] = (),
        out_paths: Collection[str] = (),
        vol_paths: Collection[str] = (),
    ) -> bool:
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
            True when inputs are readily available.
            False otherwise, meaning the step needs to be rescheduled.
        """
        self._check_node(step, "step")

        # Normalize arguments
        inp_paths = sorted(set(inp_paths))
        out_paths = sorted(set(out_paths))
        vol_paths = sorted(set(vol_paths))
        missing = set()

        # Supply required directories
        reqdirs = set()
        reqdirs.update(myparent(out_path) for out_path in out_paths)
        reqdirs.update(myparent(vol_path) for vol_path in vol_paths)
        reqdirs.difference_update(out_paths)
        reqdirs.difference_update(vol_paths)
        reqdirs.difference_update(step.out_paths())
        reqdirs.difference_update(step.vol_paths())
        for reqdir in sorted(reqdirs):
            file, available, new_idep = self.supply_file(step, reqdir, new=False)
            if not available:
                missing.add(reqdir)
            if new_idep is not None:
                self._amend_dep(new_idep)

        # Process inp_paths
        new_inp_files = []
        for inp_path in inp_paths:
            file, available, new_idep = self.supply_file(step, inp_path, new=False)
            if not available:
                missing.add(inp_path)
            if new_idep is not None:
                self._amend_dep(new_idep)
                new_inp_files.append(file)

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

        step.set_rescheduled_info("\n".join(sorted(missing)))
        return len(missing) == 0

    def _amend_dep(self, idep):
        self.con.execute("INSERT INTO amended_dep VALUES (?)", (idep,))

    def register_nglob(self, step: Step, nglob_multi: NGlobMulti):
        self._check_node(step, "step")
        step.register_nglob(nglob_multi)

    def defer_glob(self, creator: Node, pattern: str) -> list[tuple[str, FileHash]]:
        """Install a deferred glob.

        Parameters
        ----------
        creator
            The step creating the deferred glob.
        pattern
            A pattern, must be relative path without named wildcards.

        Returns
        -------
        to_check
            A list of matching (path, file_hash) whose existence and validity must be checked.
            The client must call `confirm_static` after checking files with resulting hashes.
        """
        self._check_node(creator, "creator")
        if not isinstance(pattern, str):
            raise TypeError("The argument pattern must be a string.")
        if pattern.startswith("/"):
            raise ValueError(f"Deferred glob patterns cannot be absolute paths: {pattern}")
        if any(iter_wildcard_names(pattern)):
            raise ValueError(f"Deferred glob does not support named wildcards: {pattern}")
        dg = self.create("dg", creator, pattern)
        # Check for matches in existing files.
        # For example previously defined inputs whose origin was not determined yet.
        regex = re.compile(convert_nglob_to_regex(pattern))
        matching_paths = [
            file.path
            for file in self.nodes(kind="file", include_orphans=True)
            if regex.fullmatch(file.path)
        ]
        return self.declare_missing(dg, matching_paths)

    def clean(self):
        # Get rid of deferred glob files that are no longer used.
        for dg in self.nodes(kind="dg"):
            files = sorted(dg.products(), reverse=True, key=(lambda node: node.path))
            for file in files:
                if not any(file.consumers()):
                    file.orphan()
        # Delete outputs of steps that are no longer mandatory.
        cur = self.con.execute(
            "SELECT label, digest, mode, mtime, size, inode FROM file "
            "JOIN node ON node.i = file.node "
            "JOIN dependency ON node.i = consumer "
            "JOIN step ON step.node = supplier "
            "WHERE mandatory = ? AND file.state in (?, ?, ?) AND digest != ?",
            (
                Mandatory.NO.value,
                FileState.BUILT.value,
                FileState.VOLATILE.value,
                FileState.OUTDATED.value,
                b"u",
            ),
        )
        for path, digest, mode, mtime, size, inode in cur:
            self.to_be_deleted.append((path, FileHash(digest, mode, mtime, size, inode)))
        super().clean()

    #
    # Watch phase
    #

    def is_relevant(self, path: str) -> bool:
        file, is_orphan = self.find("file", path, return_orphan=True)
        if not (file is None or is_orphan):
            return file.get_state() not in (FileState.AWAITED, FileState.VOLATILE)
        return any(ngm.may_change(set(), {path}) for ngm in self.nglob_multis())

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

    def update_nglob_multi(self, i: int, nglob_multi: NGlobMulti):
        """Store an updated nglob_multi in the database.

        Parameters
        ----------
        i
            row index of the nglob_multi to be updated.
        nglob_multi
            The new nglob_multi.
        """
        data = (pickle.dumps(nglob_multi), i)
        self.con.execute("UPDATE nglob_multi SET data = ? WHERE i = ?", data)

    def process_watcher_changes(self, deleted: Collection[str], updated: Collection[str]):
        """Update the workflow given a list of deleted and updated paths observed by the watcher.

        Parameters
        ----------
        deleted
            The deleted files.
        updated
            The added / changed files.
        """
        # Sanity check
        if not set(deleted).isdisjoint(updated):
            raise ValueError("Added and deleted files are not mutually exclusive.")

        # Process all deletions
        for path in deleted:
            # Handle deleted files that were used or created by steps.
            file = self.find("file", path)
            if file is None:
                raise ValueError("Cannot process deletion of file absent from workflow")
            file.watcher_deleted()

        # Process all updates
        for path in updated:
            file = self.find("file", path)
            # If updated the file is known, it must have changed (or a MISSING file was added).
            if file is not None:
                file.watcher_updated()

        for i, ngm, step in self.nglob_multis(yield_step=True):
            # Check if any of the deleted files matches an nglob.
            # If yes, step becomes pending.
            # Check if added files could result in new nglob matches.
            # If yes, step becomes pending.
            evolved = ngm.will_change(deleted, updated)
            if evolved is not None:
                step.delete_hash()
                self.update_nglob_multi(i, evolved)
                step.mark_pending(input_changed=True)

        # Queue pending steps that can be executed.
        sql = "SELECT i, label FROM node JOIN step ON node.i = step.node WHERE step.state = ?"
        for i, label in self.con.execute(sql, (StepState.PENDING.value,)):
            step = Step(self, i, label)
            step.queue_if_appropriate()
