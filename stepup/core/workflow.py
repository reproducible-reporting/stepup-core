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
"""The `Workflow` is a `Trellis` subclass with more concrete node implementations."""

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

from .enums import FileState, HashUpdateCause, Need, StepState
from .exceptions import GraphError
from .file import File
from .hash import FileHash, fmt_digest
from .nglob import NGlobMulti, has_wildcards
from .sqlite3 import escape_like_pattern
from .static_tree import StaticTree
from .step import RESERVED_ENV_VARS, Step
from .trellis import Node, Root, Trellis
from .utils import string_to_bool

__all__ = ("Workflow",)


logger = logging.getLogger(__name__)


# Find all inputs of steps (recursively through creator-product relations) that are missing,
# and whose creator is a static tree.
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
SELECT i, label FROM missing WHERE creator_kind = 'st'
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
    """Result of the `supply_files` method, for internal use only."""

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

    These are typically new matches of a static tree.
    """

    new_idep: int | None = attrs.field()
    """Dependency identifier when the relation is new, None otherwise."""


@attrs.define(eq=False)
class Workflow(Trellis):
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

        # Verify that succeeded steps have no rescheduled info.
        sql = (
            "SELECT step.node, node.label FROM step JOIN node ON step.node = node.i "
            "WHERE step.state = ? AND step.rescheduled_info != '' AND NOT node.detached"
        )
        data = (StepState.SUCCEEDED.value,)
        for si, slabel in self.db.execute(sql, data):
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
        to_check = self.declare_missing(self.root, ["plan.py"])
        checked = [(path, file_hash.regen(path)) for path, file_hash in to_check]
        self.update_file_hashes(checked, HashUpdateCause.CONFIRMED)
        self.define_step(self.root, command, inp_paths=["plan.py"], need=Need.PLAN, safe=True)
        return True

    @staticmethod
    def default_node_classes() -> list[type[Node]]:
        return [*Trellis.default_node_classes(), File, Step, StaticTree]

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

    def detached_inp_paths(self) -> Iterator[str, FileState]:
        """Iterate over detached input paths used by non-detached steps."""
        sql = (
            "SELECT node.label, file.state FROM node JOIN file ON node.i = file.node "
            "WHERE node.detached "
            "AND EXISTS (SELECT 1 FROM dependency JOIN node ON node.i = dependency.consumer "
            "WHERE supplier = file.node AND not node.detached)"
        )
        for row in self.db.execute(sql):
            yield row[0], FileState(row[1])

    def missing_paths(self) -> Iterator[str]:
        """Iterate over static files that have been deleted or were never confirmed."""
        sql = (
            "SELECT label FROM node JOIN file ON node.i = file.node "
            "WHERE state = ? AND NOT detached"
        )
        for row in self.db.execute(sql, (FileState.MISSING.value,)):
            yield row[0]

    #
    # Build phase
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

    def _check_supplier(self, supplier: Node, consumer: Node) -> None:
        super()._check_supplier(supplier, consumer)
        if (
            (isinstance(supplier, File) and not isinstance(consumer, Step))
            or (isinstance(supplier, Step) and not isinstance(consumer, File))
            or (isinstance(supplier, StaticTree) and not isinstance(consumer, File))
        ):
            raise GraphError(
                f"Node {consumer.key()!r} (kind={consumer.kind()!r}) "
                "cannot be a dependency consumer"
            )

    def matching_static_tree(self, path: str) -> StaticTree | None:
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

    def _resolve_supply_file(
        self, node: Node, path: str, new: bool
    ) -> tuple[File, bool, list[Node], bool]:
        """Find or create the file for a path and resolve its relation to node.

        This performs everything `supply_files` needs except inserting the
        dependency edge, so the cyclic-dependency check can be batched
        across multiple paths by the caller.

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
        file
            The existing or newly created file node.
        available
            See `SupplyInfo.available`.
        deferred
            See `SupplyInfo.deferred`.
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
        deferred = []
        if file is None or detached:
            st = self.matching_static_tree(path)
            if st is None:
                file = self.create(File, None, path, state=FileState.AWAITED)
            else:
                file = self.create(File, st, path, state=FileState.MISSING)
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
            self.db.execute(
                "SELECT 1 FROM dependency WHERE supplier = ? AND consumer = ?", (file.i, node.i)
            ).fetchone()
            is None
        )
        if not new_relation and new:
            raise GraphError(f"Supplying file already exists: {path}")
        return file, available, deferred, new_relation

    def supply_files(
        self, node: Node, paths: Collection[str], new: bool = True
    ) -> list[SupplyInfo]:
        """Find or create files for several paths and make them suppliers of node.

        Since `node` is the consumer of every new edge in this batch,
        the cyclic-dependency check is performed once for the whole batch
        (via `Node.check_no_cycle_batch`) instead of once per path.
        Note that if `paths` contains a duplicate, it is caught later than before:
        as a `GraphError("Relation already exists")` from `add_supplier` instead of
        `GraphError("Supplying file already exists")`.
        This is unreachable in practice because callers already dedupe `paths`.

        Parameters
        ----------
        node
            The file or step node to supply to.
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
        if new_file_is:
            node.check_no_cycle_batch(new_file_is)
        results = []
        for file, available, deferred, new_relation in resolved:
            new_idep = node.add_supplier(file, skip_cycle_check=True) if new_relation else None
            results.append(SupplyInfo(file, available, deferred, new_idep))
        return results

    def declare_file(
        self, creator: Node, path: str, file_state: FileState
    ) -> tuple[Node, list[Node]]:
        """Create (or recycle) a file with a MISSING, AWAITED or VOLATILE file state.

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
            raise ValueError("Cannot create a STATIC file. It must be MISSING first.")
        if file_state == FileState.VOLATILE and path.endswith(os.sep):
            raise GraphError("A volatile output cannot be a directory.")
        if not (creator.kind() == "st" or self.matching_static_tree(path) is None):
            raise GraphError("Cannot manually add a file that matches a static tree.")

        file = self.create(File, creator, path, state=file_state)

        if file_state == FileState.VOLATILE:
            # Do not allow volatile files to have consumers.
            if any(file.consumers()):
                raise GraphError(f"An input to an existing step cannot be volatile: {path}")
        else:
            # Watch parent directories of non-volatile files.
            self.put_dir_queue(Path(path).parent)
        return file

    def put_dir_queue(self, path: str):
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
        # Sort paths to make the operation deterministic.
        paths = sorted(set(paths))
        # Define the files and create a list of (path, file_hash) tuples.
        missing = [self.declare_file(creator, path, FileState.MISSING) for path in paths]
        # Collect a list of paths and file hashes to be checked.
        return self._build_to_check(missing)

    def _build_to_check(self, deferred: Collection[Node]) -> list[tuple[str, FileHash]]:
        """Convert a list of MISSING file nodes to a list of (path, file_hash) tuples.

        This list is intended to be returned to the caller, so the validity of the files
        can be checked and confirmed as static in a follow-up RPC call.

        Parameters
        ----------
        deferred
            MISSING file nodes that match a static tree.

        Returns
        -------
        to_check
            A list of paths and file_hashes.
            These must be sent back to the client where the hashes can be checked
            and which then calls `confirm_hashes` with the updated hashes.
        """
        self.db.execute("DROP TABLE IF EXISTS temp.missing")
        try:
            self.db.execute("CREATE TABLE temp.missing(node INTEGER PRIMARY KEY)")
            sql = "INSERT INTO temp.missing VALUES (?)"
            self.db.executemany(sql, ((file.i,) for file in deferred))
            sql = (
                "SELECT label, hash "
                "FROM temp.missing "
                "JOIN node ON node.i = temp.missing.node "
                "JOIN file ON file.node = temp.missing.node"
            )
            return [
                (path, FileHash.from_json(hash_value)) for path, hash_value in self.db.execute(sql)
            ]
        finally:
            self.db.execute("DROP TABLE IF EXISTS temp.missing")

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
        self.db.execute("DROP TABLE IF EXISTS temp.paths")
        try:
            self.db.execute("CREATE TABLE temp.paths(path TEXT PRIMARY KEY)")
            self.db.executemany("INSERT INTO temp.paths VALUES (?)", ((path,) for path in paths))
            sql = (
                "SELECT label, hash FROM node "
                "JOIN file ON file.node = node.i JOIN temp.paths ON label = temp.paths.path"
            )
            return [
                (path, FileHash.from_json(hash_value)) for path, hash_value in self.db.execute(sql)
            ]
        finally:
            self.db.execute("DROP TABLE IF EXISTS temp.paths")

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
        self.db.execute("DROP TABLE IF EXISTS temp.updated")
        try:
            self.db.execute("CREATE TABLE temp.updated(path TEXT PRIMARY KEY) WITHOUT ROWID")
            self.db.executemany(
                "INSERT INTO temp.updated VALUES (?)",
                ((path,) for path in file_hashes),
            )
            sql = (
                "SELECT node.i, path, file.state FROM temp.updated "
                "JOIN node ON node.label = path JOIN file ON file.node = node.i "
            )
            records = [
                (i, path, file_hashes[path], FileState(value))
                for i, path, value in self.db.execute(sql)
            ]
        finally:
            self.db.execute("DROP TABLE IF EXISTS temp.updated")

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
        if cause == HashUpdateCause.EXTERNAL:
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
        elif cause == HashUpdateCause.SUCCEEDED:
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
        elif cause == HashUpdateCause.FAILED:
            # This branch is relevant for when a step has failed or rescheduled
            # and its outputs should be marked as OUTDATED.
            for i, path, new_fh, old_state in records:
                if old_state in (FileState.OUTDATED, FileState.AWAITED):
                    new_states_hashes.append(
                        (i, FileState.AWAITED if new_fh.is_unknown else FileState.OUTDATED, new_fh)
                    )
                else:
                    raise_unexpected(path, old_state, new_fh)
        elif cause == HashUpdateCause.CONFIRMED:
            # This branch is relevant for when the client has confirmed the hashes
            # of missing files and they should be marked as STATIC.
            for i, path, new_fh, old_state in records:
                if old_state == FileState.MISSING:
                    if new_fh.is_unknown:
                        raise AssertionError(f"Missing file confirmed as missing: {path}")
                    new_states_hashes.append((i, FileState.STATIC, new_fh))
                    completed.append((i, path))
                elif old_state == FileState.STATIC:
                    # Two steps can race to be the first to use the same static-tree file:
                    # both get told to check and confirm it before either confirmation
                    # is processed. The second confirmation to arrive is a harmless
                    # duplicate of the first.
                    if new_fh.is_unknown:
                        raise AssertionError(f"Static file confirmed as missing: {path}")
                    new_states_hashes.append((i, FileState.STATIC, new_fh))
                else:
                    raise_unexpected(path, old_state, new_fh)

        # Actual update of the file hashes.
        logger.info("Update file hashes: cause=%s new=%s", cause, new_states_hashes)
        if len(new_states_hashes) != len(file_hashes):
            raise AssertionError(
                f"Inconsistent number of file hash updates: "
                f"expected={len(file_hashes)} actual={len(new_states_hashes)}"
            )
        self.db.executemany(
            "UPDATE file SET state = ?, hash = ? WHERE node = ?",
            ((state.value, fh.to_json(), i) for i, state, fh in new_states_hashes),
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
        # If it is a boot step, check that there was no boot step yet.
        if creator.i == self.root.i and any(self.root.products(Step)):
            raise GraphError("Boot step already defined.")

        # Normalize arguments
        inp_paths = sorted(set(inp_paths))
        env_deps = sorted(set(env_deps))
        out_paths = sorted(set(out_paths))
        vol_paths = sorted(set(vol_paths))
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

        # If a matching detached step is found, reuse it, instead of creating a new one.
        old_deferred = self._recreate_step(
            command,
            workdir,
            inp_paths,
            env_deps,
            out_paths,
            vol_paths,
            need,
            resources,
            creator,
            subshell,
            env_overrides,
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
        step.set_env_overrides(env_overrides)

        # Keep track of all missing files that match a static tree and need to be confirmed.
        deferred = set()

        # Supply inp_paths
        for info in self.supply_files(step, inp_paths):
            # We do not care about the unavailable files here,
            # because the step will only be executed when all inputs are available.
            deferred.update(info.deferred)

        # Process vars
        step.add_env_deps(env_deps)

        # Create out_paths
        for out_path in out_paths:
            file = self.declare_file(step, out_path, FileState.AWAITED)
            file.add_supplier(step)

        # Create vol_paths
        for vol_path in vol_paths:
            file = self.declare_file(step, vol_path, FileState.VOLATILE)
            file.add_supplier(step)

        # Determine if the step needs executing and queue if relevant.
        logger.info("Define step: %s", step.label)
        return self._build_to_check(deferred)

    def _recreate_step(
        self,
        command: str,
        workdir: str,
        inp_paths: list[str],
        env_deps: list[str],
        out_paths: list[str],
        vol_paths: list[str],
        need: Need,
        resources: dict[str, int] | None,
        creator: Node,
        subshell: bool = False,
        env_overrides: dict[str, str] | None = None,
    ) -> set[Node] | None:
        """Recreate a step if it was detached and the step arguments are compatible.

        Returns
        -------
        deferred
            If the step can be reused, a possibly empty list is returned with
            MISSING file nodes that match a static tree and need to be confirmed.
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
        old_env_vars = sorted(old_step.env_deps(amended=False))
        if old_env_vars != env_deps:
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
        self.db.execute(
            "UPDATE step SET need = ?, subshell = ?, _check_safe = 1, _check_after = 1 "
            "WHERE node = ?",
            (need.value, int(subshell), old_step.i),
        )

        # Restore the step and its products (recursively), and set resources and overrides.
        old_step.recycle(creator)
        old_step.set_resources(resources)
        old_step.set_env_overrides(env_overrides)

        # If inputs of the recreated steps are AWAITED or OUTDATED, these steps must be rescheduled.
        for i, label in self.db.execute(RECURSE_OUTDATED_STEPS, (old_step.i,)):
            step = Step(self, i, label)
            step.mark_pending()

        # Look for MISSING inputs and determine which were matching a static tree.
        # Their existence still needs to be checked by the client and ideally confirmed as existing
        # in a follow-up call to `confirm_hashes`.
        deferred = {
            File(self, i, label)
            for i, label in self.db.execute(RECURSE_DEFERRED_INPUTS, (old_step.i,))
        }

        logger.info("Reuse detached step: %s", old_step.label)
        return deferred

    def amend_step(
        self,
        step: Step,
        *,
        inp_paths: Collection[str] = (),
        env_deps: Collection[str] = (),
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
        env_deps
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
        if not isinstance(step, Step):
            raise TypeError(f"step must be a Step instance, got: {step!r}")
        if step.is_detached():
            raise GraphError(f"step is detached: '{step.key()}'")

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
        infos = self.supply_files(step, inp_paths, new=False)
        for inp_path, info in zip(inp_paths, infos, strict=True):
            if not info.available:
                unavailable.add(inp_path)
            if info.new_idep is not None:
                self._amend_dep(info.new_idep)
            deferred.update(info.deferred)

        # Process vars
        step.amend_env_deps(env_deps)

        # Create out_paths
        for out_path in out_paths:
            file = self.declare_file(step, out_path, FileState.AWAITED)
            new_idep = file.add_supplier(step)
            self._amend_dep(new_idep)

        # Create vol_paths
        for vol_path in vol_paths:
            file = self.declare_file(step, vol_path, FileState.VOLATILE)
            new_idep = file.add_supplier(step)
            self._amend_dep(new_idep)

        if len(unavailable) > 0:
            step.add_rescheduled_info("\n".join(sorted(unavailable)))

        return len(unavailable) == 0, self._build_to_check(deferred)

    def _amend_dep(self, idep):
        self.db.execute("INSERT INTO amended_dep VALUES (?)", (idep,))

    def register_nglob(self, step: Step, nglob_multi: NGlobMulti):
        if not isinstance(step, Step):
            raise TypeError(f"step must be a Step instance, got: {step!r}")
        if step.is_detached():
            raise GraphError(f"step is detached: '{step.key()}'")
        step.register_nglob(nglob_multi)

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
        path = Path(path) / ""
        if self.matching_static_tree(path) is not None:
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
        pattern = f"{escape_like_pattern(path)}%"
        sql = (
            "SELECT label FROM node JOIN file ON node.i = file.node "
            f"WHERE state != {FileState.MISSING.value} "
            "AND node.label LIKE ?"
        )
        matching_paths = [path for (path,) in self.db.execute(sql, (pattern,))]
        return self.declare_missing(st, matching_paths)

    def clean(self):
        # Get rid of static tree files that are no longer used.
        for st in self.nodes(StaticTree):
            files = sorted(st.products(), reverse=True, key=(lambda node: node.path))
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
                step.mark_pending()
