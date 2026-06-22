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
"""StepUp's abstract implementation of the provenance and dependency graphs."""

import inspect
import logging
import sqlite3
import sys
from collections.abc import Iterable, Iterator
from typing import Self, TypeVar

import attrs

from .exceptions import CyclicError, GraphError
from .sqlite3 import wipe_database

__all__ = ("Cascade", "Node", "Root")


logger = logging.getLogger(__name__)


CASCADE_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA application_id={application_id};
PRAGMA user_version={schema_version};
PRAGMA synchronous=OFF;

CREATE TABLE IF NOT EXISTS node (
    i INTEGER PRIMARY KEY,
    -- Unique integer identifier of the node.
    kind TEXT NOT NULL,
    -- Type of node, e.g. file, step, ...
    label TEXT NOT NULL,
    -- User-provided label, unique within its kind.
    creator INTEGER,
    -- Node that creates this node.
    detached BOOLEAN NOT NULL DEFAULT FALSE CHECK (detached IN (FALSE, TRUE)),
    -- Flag indicating that creator is NULL of this node or its (indirect) creator.
    -- This is kept up-to-date by the detach and recycle methods of Node.
    -- Detached nodes are used for various purposes in StepUp:
    -- * To determine whether a node should be cleaned up.
    -- * To exclude detached steps from scheduling.
    -- * To keep metadata about detached nodes in case they are recycled later.
    FOREIGN KEY (creator) REFERENCES node(i)
);
CREATE INDEX IF NOT EXISTS node_kind ON node (kind);
CREATE INDEX IF NOT EXISTS node_creator ON node (creator);
CREATE UNIQUE INDEX IF NOT EXISTS node_kind_label ON node (kind, label);

CREATE TABLE IF NOT EXISTS dependency (
    i INTEGER PRIMARY KEY,
    supplier INTEGER NOT NULL,
    consumer INTEGER NOT NULL,
    UNIQUE (supplier,consumer),
    FOREIGN KEY (supplier) REFERENCES node(i),
    FOREIGN KEY (consumer) REFERENCES node(i)
);
CREATE INDEX IF NOT EXISTS dependency_supplier_consumer ON dependency(supplier, consumer);
CREATE INDEX IF NOT EXISTS dependency_consumer_supplier ON dependency(consumer, supplier);
"""

# Recursively find all products of a node and mark them as detached or non-detached.
RECURSIVELY_SET_DETACHED = """
WITH RECURSIVE all_products(current, kind, label) AS (
    -- Initial: Select first generation of products
    SELECT i AS current, kind, label
    FROM node WHERE creator = ?
    UNION
    -- Recursion: Follow creator -> product edges by selecting products of current
    SELECT node.i AS current, node.kind, node.label
    FROM node INNER JOIN all_products ON creator = current
)
UPDATE node SET detached = ? WHERE i IN (SELECT current FROM all_products)
"""

INITIAL_CONSUMERS = "CREATE TABLE temp.initial_consumer (current INTEGER PRIMARY KEY) WITHOUT ROWID"

RECURSE_CONSUMERS = """
WITH RECURSIVE all_consumer(current) AS (
    -- Initial: Set initial node
    SELECT current
    FROM temp.initial_consumer
    UNION
    -- Recursion: Follow edges by selecting consumers of current
    SELECT consumer AS current
    FROM dependency INNER JOIN all_consumer ON supplier = current
)
"""

SELECT_CYCLIC = """
-- Final: Check if any of the (indirect) consumer matches the supplier in the new edge
SELECT EXISTS (SELECT 1 FROM all_consumer WHERE current = ?)
"""

SELECT_WALK = """
-- Final: Get all (indirect) consumers of a node.
SELECT current FROM all_consumer
"""

DROP_CONSUMERS = "DROP TABLE IF EXISTS temp.initial_consumer"


NodeType = TypeVar("NodeType")


@attrs.define(frozen=True)
class Node:
    """Base class for nodes in the provenance and dependency graphs.

    Instances of this object are merely references to information in the database.
    These are typically short-lived objects.
    They only store a few immutable pieces of information:

    - `con`: the SQLite connnection.
    - `i`: the identifier of the node in the database.
    - `kind`: determines the subclass of `Node` to use.
    - `label`: a unique (within its kind) label for the node.

    All other information related to this node is directly taken from or stored in the database.

    Subclasses may override the following:

    - `key_prefix` to control the formatting of the key string.
    - `schema` to extend the cascade schema.
    - `create_label` to override the user-provided label of a node.
    - `initialize` to create or update rows for new nodes outside the default Cascade tables.
    - `validate` to check if the necessary rows outside the default Cascade tables are made.
    - `format_properties` to define the properties of the node.
    - `give_up` is called when a detached node must be cleaned up because it loses a product node.
    - `clean` to decide if a detached node can be removed and to release resources.
    """

    cascade: "Cascade" = attrs.field(repr=False)
    """The Cascade object that contains the node."""

    i: int = attrs.field()
    """The identifier of the node in the database."""

    label: str = attrs.field()
    """The label of the node.

    While this can be derived from the database, it is stored here for convenience,
    since it is considered immutable.
    """

    @property
    def con(self) -> sqlite3.Connection:
        """The SQLite database."""
        return self.cascade.con

    @classmethod
    def kind(cls) -> str:
        """Lower-case prefix of the key string representing a node."""
        return cls.__name__.lower()

    def key(self, detached: bool = False) -> str:
        """Return the key representation of the node, for terminal display."""
        result = f"{self.kind()}:{self.label}"
        if detached:
            result = f"({result})"
        return result

    @classmethod
    def schema(cls) -> str | None:
        """Return node-specific SQL commands to initialize the database."""
        return None

    @classmethod
    def create_label(cls, label: str, **kwargs):
        """Optionally override the user-provided label when creating a node."""
        return str(label)

    def initialize(self, **kwargs):
        """Create extra information in the database about this node."""

    def validate(self):
        """Validate that extra information about this node is present in the database."""

    def format_properties(self) -> Iterator[tuple[str, str]]:
        """Iterate over key-value pairs with the properties of the node, for terminal display."""
        yield from []

    def give_up(self):
        """Clean up a detached node because it loses a product node.

        Implementations in subclasses should remove all related info,
        all edges, and make sure the node is deleted.
        """
        raise NotImplementedError

    def clean(self):
        """Perform a cleanup right before the detached node is removed from the graph."""

    #
    # Getters and Iterators
    #

    def is_alive(self) -> bool:
        """True when the node is still present in the database."""
        return self.con.execute("SELECT 1 FROM node WHERE i = ?", (self.i,)).fetchone() is not None

    def is_detached(self) -> bool:
        """True when the node or its creator (recursively) lost its creator node."""
        row = self.con.execute("SELECT detached FROM node WHERE i = ?", (self.i,)).fetchone()
        return bool(row[0])

    def creator(self) -> Self | None:
        """Return the creator of the node."""
        row = self.con.execute(
            "SELECT node.i, node.kind, node.label "
            "FROM node WHERE node.i = (SELECT creator FROM node WHERE i = ?)",
            (self.i,),
        ).fetchone()
        if row is None:
            return None
        i, kind, label = row
        return self.cascade._node_classes[kind](self.cascade, i, label)

    def creator_detached(self) -> tuple[Self, bool] | tuple[None, None]:
        """Return the creator of the node.

        Returns
        -------
        creator
            The creator node, or `None` if there is no creator.
        detached
            Whether the creator node is detached.
        """
        row = self.con.execute(
            "SELECT i, kind, label, detached "
            "FROM node WHERE i = (SELECT creator FROM node WHERE i = ?)",
            (self.i,),
        ).fetchone()
        if row is None:
            return None, None
        i, kind, label, detached = row
        return self.cascade._node_classes[kind](self.cascade, i, label), detached

    def products(self, node_type: type[NodeType] = Self) -> Iterator[NodeType]:
        """Iterate over (a subset of) products of this node."""
        query = "SELECT i, kind, label FROM node WHERE creator = ?"
        data = [self.i]
        if node_type is not Self:
            query += " AND kind = ?"
            data.append(node_type.kind())
        for i, kind, label in self.con.execute(query, data):
            yield self.cascade.node_classes[kind](self.cascade, i, label)

    def products_str(self, node_type: type[NodeType] = Self) -> Iterator[str]:
        """Iterate over (a subset of) products of this node, formatted as strings."""
        sql = "SELECT kind, label, detached FROM node WHERE creator = ?"
        data = [self.i]
        if node_type is not Self:
            sql += " AND kind = ?"
            data.append(node_type.kind())
        sql += " ORDER BY kind, label"
        for kind, label, detached in self.con.execute(sql, data):
            node_str = f"{kind}:{label}"
            if detached:
                node_str = f"({node_str})"
            yield node_str

    def _dependencies(
        self,
        node_type: type[NodeType] = Self,
        include_detached: bool = False,
        do_suppliers: bool = True,
    ) -> Iterator[NodeType]:
        sql = "SELECT node.i, kind, label FROM node JOIN dependency ON node.i = "
        if do_suppliers:
            sql += "supplier WHERE consumer = ?"
        else:
            sql += "consumer WHERE supplier = ?"
        data = [self.i]
        if node_type is not Self:
            sql += " AND kind = ?"
            data.append(node_type.kind())
        if not include_detached:
            sql += " AND NOT detached"
        for i, kind, label in self.cascade.con.execute(sql, data):
            yield self.cascade.node_classes[kind](self.cascade, i, label)

    def suppliers(
        self, node_type: type[NodeType] = Self, include_detached: bool = False
    ) -> Iterator[NodeType]:
        """Iterate over nodes that supply to this one."""
        yield from self._dependencies(node_type, include_detached, do_suppliers=True)

    def consumers(
        self, node_type: type[NodeType] = Self, include_detached: bool = False
    ) -> Iterator[NodeType]:
        """Iterate over nodes that consume from this one."""
        yield from self._dependencies(node_type, include_detached, do_suppliers=False)

    def _dependencies_str(
        self,
        node_type: type[NodeType] = Self,
        do_suppliers: bool = True,
    ) -> Iterator[tuple[int, str]]:
        sql = "SELECT kind, label, detached, dependency.i FROM node JOIN dependency ON node.i ="
        if do_suppliers:
            sql += " supplier WHERE consumer = ?"
        else:
            sql += " consumer WHERE supplier = ?"
        data = [self.i]
        if node_type is not Self:
            sql += " AND kind = ?"
            data.append(node_type.kind())
        sql += " ORDER BY kind, label"
        for kind, label, detached, idep in self.cascade.con.execute(sql, data):
            node_str = f"{kind}:{label}"
            if detached:
                node_str = f"({node_str})"
            yield idep, node_str

    def suppliers_str(self, node_type: type[NodeType] = Self) -> Iterator[tuple[int, str]]:
        """Iterate over nodes that supply to this one, formatted as strings."""
        yield from self._dependencies_str(node_type, do_suppliers=True)

    def consumers_str(self, node_type: type[NodeType] = Self) -> Iterator[tuple[int, str]]:
        """Iterate over nodes that consume from this one, formatted as strings."""
        yield from self._dependencies_str(node_type, do_suppliers=False)

    #
    # Graph modifications
    #

    def detach(self):
        """Mark node as no longer being created, disconnect from its creator node.

        Detached nodes will have their creator set to NULL in the database.
        Actual deletion may take place when calling the clean method.

        When a node is detached, its `detached` field in the node table is set to `True`,
        and this property is propagated recursively to all its product nodes.

        Raises
        ------
        ValueError
            If the node is not found in the database.
        """
        row = self.con.execute(
            "SELECT creator, detached FROM node WHERE i = ?", (self.i,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Node id not in database: {self.i}")
        creator_i, detached = row
        if creator_i is not None:
            self.con.execute(
                "UPDATE node SET creator = NULL, detached = TRUE WHERE i = ?", (self.i,)
            )
            if not detached:
                self.con.execute(RECURSIVELY_SET_DETACHED, (self.i, True))

    def recycle(self, new_creator: Self):
        """Reconnect the node to a new creator node, preserving its properties.

        This method is used to reattach a detached node to a new creator node.

        Raises
        ------
        ValueError
            If the node is not detached or the new creator is detached.
        TypeError
            If the new_creator is not an instance of Node.
        """
        if not self.is_detached():
            raise ValueError("Node.recycle can only be called on a detached node.")
        if not isinstance(new_creator, Node):
            raise TypeError(f"Argument new_creator must be a Node, got {type(new_creator)}")
        if new_creator.is_detached():
            raise ValueError("New creator node must not be detached.")
        self.cascade._check_creator(type(self), new_creator)
        old_creator, old_creator_detached = self.creator_detached()
        self.con.execute(
            "UPDATE node SET creator = ?, detached = FALSE WHERE i = ?", (new_creator.i, self.i)
        )
        # Clean up the old creator if it exists.
        if old_creator is not None:
            if not old_creator_detached:
                raise GraphError("Old creator of detached node is not detached.")
            old_creator.give_up()
        # Propagate the detached=FALSE property to all product nodes.
        self.con.execute(RECURSIVELY_SET_DETACHED, (self.i, False))

    def add_supplier(self, supplier: Self) -> int:
        """Add a supplier-consumer relation.

        Parameters
        ----------
        supplier
            Other node that supplies to this node.

        Returns
        -------
        idep
            The identifier in the dependency table.
        """
        self.cascade._check_supplier(supplier, self)
        # Check whether the new edge would introduce a cyclic dependency.
        self.con.execute(DROP_CONSUMERS)
        self.con.execute(INITIAL_CONSUMERS)
        try:
            self.con.execute("INSERT INTO temp.initial_consumer VALUES(?)", (self.i,))
            cur = self.con.execute(RECURSE_CONSUMERS + SELECT_CYCLIC, (supplier.i,))
            if cur.fetchone()[0] > 0:
                raise CyclicError("New relation introduces a cyclic dependency")
        finally:
            self.con.execute(DROP_CONSUMERS)
        try:
            cur = self.con.execute(
                "INSERT INTO dependency(supplier, consumer) VALUES(?, ?)",
                (supplier.i, self.i),
            )
        except sqlite3.IntegrityError as exc:
            raise GraphError("Relation already exists") from exc
        return cur.lastrowid

    def del_suppliers(self, suppliers: list[Self] | None = None):
        """Delete given suppliers.

        Without arguments, all suppliers of the current node are deleted.
        """
        if suppliers is None:
            self.con.execute("DELETE FROM dependency WHERE consumer = ?", (self.i,))
        else:
            self.con.executemany(
                "DELETE FROM dependency WHERE supplier = ? AND consumer = ?",
                ((supplier.i, self.i) for supplier in suppliers),
            )


@attrs.define(frozen=True)
class Root(Node):
    """The root node of the provenance and dependency graph.

    (Indirect) products of the root node are considered active nodes in the graph.
    Nodes that are not connected (indirectly) to the root node are considered detached,
    and will be removed when the Cascade.clean method is called.
    """

    def clean(self):
        """Perform a cleanup right before the detached node is removed from the graph."""
        raise AssertionError("Root node cannot be cleaned")

    def give_up(self):
        """Clean up a detached node because it loses a product node."""
        raise AssertionError("Root node cannot be detached because that would mean it is detached.")


@attrs.define
class ConnectionWrapper:
    """Wrapper for SQLite connection that prints from where execute is called."""

    _con: sqlite3.Connection = attrs.field()
    """SQLite connection where the graph is stored."""

    _explain: bool = attrs.field(default=False)
    """If set to `True`, each query plan is printed to stderr."""

    def _print_caller(self, name: str):
        frame = inspect.currentframe().f_back.f_back
        module = frame.f_globals.get("__name__", "Unknown Module")
        lineno = frame.f_lineno
        print(f"sqlite3 {name} called from {module}:{lineno}", file=sys.stderr)

    def execute(self, sql: str, data: tuple = ()):
        self._print_caller("execute")
        if self._explain:
            print(sql, file=sys.stderr)
            print(f"  {data}", file=sys.stderr)
            nodes = {}
            # Local imports to avoid dependency for production usage.
            from anytree import Node as TreeNode  # noqa: PLC0415
            from anytree import RenderTree  # noqa: PLC0415

            for step in self._con.execute(f"EXPLAIN QUERY PLAN {sql}", data):
                node_id, parent_id, _, detail = step
                nodes[node_id] = TreeNode(f"({node_id}) {detail}", parent=nodes.get(parent_id))
            root_nodes = [node for node in nodes.values() if node.parent is None]
            for root in root_nodes:
                for pre, _, node in RenderTree(root):
                    print(f"{pre}{node.name}", file=sys.stderr)
            print("#" * 80, file=sys.stderr)
            print(file=sys.stderr)
        return self._con.execute(sql, data)

    def executemany(self, sql: str, data: list[tuple]):
        self._print_caller("executemany")
        return self._con.executemany(sql, data)

    def __enter__(self):
        return self._con.__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        self._con.__exit__(exc_type, exc_value, traceback)


@attrs.define(eq=False)
class Cascade:
    """Base class for provenance and denpendency graphs.

    Subclasses should implement at least the following:

    - Override `default_node_classes` to define the types of nodes that are supported.
    """

    # SQLite database
    _con: sqlite3.Connection = attrs.field()
    _con_wrapper: ConnectionWrapper = attrs.field(init=False)

    @_con_wrapper.default
    def _default_con_wrapper(self) -> ConnectionWrapper:
        return ConnectionWrapper(self._con, True)

    # The types of nodes that are supported.
    _node_classes: dict[type[Node], str] = attrs.field(init=False)

    @_node_classes.default
    def _default_node_classes(self) -> dict[type[Node], str]:
        return {node_class.kind(): node_class for node_class in self.default_node_classes()}

    @staticmethod
    def default_node_classes() -> list[type[Node]]:
        return [Root]

    @property
    def node_classes(self) -> list[type[Node]]:
        return self._node_classes

    # Pre-fetched results from the database
    _root: Root = attrs.field(init=False)

    #
    # Initialization
    #

    @property
    def application_id(self) -> int:
        """Return the application ID of the database.

        This can be used to recognize the database file as a StepUp database.
        """
        return 768739001

    @property
    def schema_version(self) -> int:
        """Return the schema version of the database."""
        # Schema 1 became outdated due to new step_hash table.
        # While making this change, the enums were also made more intuitive.
        # Schema 2 became outdated due to the worker actions.
        # Schema 3 became outdated due to a change in step table (dirty field).
        # Schema 4 became outdated due to:
        # - the use of PARSE_DECLTYPES instead of PARSE_COLNAMES,
        #   which requires using UINT64 for file inodes.
        # - Directory file nodes are no longer supported.
        # - Renamed "orphan" to "detached".
        # - Added "_safe" and "_check_safe" fields to step table.
        # - Added "need", "_implied_need", "_tail_time" and "_check_after" fields to step table.
        # - Removed the "dirty" field from the step table.
        #   (The rescheduled_info is used instaed.)
        # - The QUEUED step state has been removed.
        # - Switch from Blake2B to SHA-256 hashes
        # - Added "subshell" field to step table (replaces the actions concept with a shell flag).
        # - Step labels no longer carry an action-name prefix; they store the raw command line.
        #   This changes all inp_digest values in step_hash, invalidating all cached step results.
        # - Added CHECKING step state (value 25): steps being hash-checked for possible skipping
        #   without consuming named resource slots.
        #   The STEP_SCHEMA CHECK constraint was updated from <= 24 to <= 25.

        return 5

    @classmethod
    def schema(cls) -> str:
        """Return the SQL schema for the database. (Does not include node-specific schemas.)"""
        return CASCADE_SCHEMA

    def __attrs_post_init__(self):
        """Initialize or check the initial database.

        Returns
        -------
        initialized
            True when the database was (re)initialized from scratch.
        """
        empty = self._con.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
        if not empty:
            rows = self._con.execute("PRAGMA application_id").fetchone()
            if len(rows) != 1 or rows[0] != self.application_id:
                raise ValueError("Invalid database application ID")
            schema_version = self._con.execute("PRAGMA user_version").fetchone()[0]
            if schema_version != self.schema_version:
                wipe_database(self._con)
                empty = True
        self._con.executescript(
            self.schema().format(
                application_id=self.application_id,
                schema_version=self.schema_version,
            )
        )
        for node_class in self._node_classes.values():
            node_schema = node_class.schema()
            if node_schema is not None:
                self._con.executescript(node_schema)
        if empty:
            self._con.execute("VACUUM")
            self._root = self.create(Root, None)
        else:
            self._root = self.find(Root, "")
            self.check_consistency()

    def check_consistency(self):
        """Check whether the graph satisfies all constraints."""
        if self._root.creator() != self._root:
            raise GraphError("Invalid cascade: root node does not create itself")
        if self._root.is_detached():
            raise GraphError("Invalid cascade: root node cannot be detached")
        sql = (
            "SELECT node.i, node.kind, node.label, node.creator, node.detached, cnode.detached "
            "FROM node LEFT JOIN node AS cnode ON node.creator = cnode.i"
        )
        for row in self.con.execute(sql):
            i, kind, label, creator_i, detached, creator_detached = row
            if i > 1 and creator_i == i:
                node = self._node_classes[kind](self, i, label)
                raise GraphError(f"Non-root node is its own creator: {node.key()}")
            creator_detached = creator_i is None or creator_detached
            if detached:
                if not creator_detached:
                    node = self._node_classes[kind](self, i, label)
                    raise GraphError(f"Detached node has attached creator: {node.key()}")
            elif creator_detached:
                node = self._node_classes[kind](self, i, label)
                raise GraphError(f"Attached node has detached creator: {node.key()}")
        for node in self.nodes():
            node.validate()

    #
    # Basic attributes and introspection
    #

    @property
    def con(self) -> sqlite3.Connection:
        """Access the connection to the SQLite database."""
        # return self._con_wrapper
        return self._con

    @property
    def root(self) -> Root:
        return self._root

    def find(self, node_type: type[NodeType], label: str) -> NodeType | None:
        """Return the node for the given node class and label or index."""
        sql = "SELECT i FROM node WHERE kind = ? AND label = ?"
        data = (node_type.kind(), label)
        row = self._con.execute(sql, data).fetchone()
        return None if row is None else node_type(self, row[0], label)

    def find_detached(
        self, node_type: type[NodeType], label: str
    ) -> tuple[NodeType, bool] | tuple[None, None]:
        """Return the node and detached flag for the given node class and label."""
        sql = "SELECT i, detached FROM node WHERE kind = ? AND label = ?"
        data = (node_type.kind(), label)
        row = self._con.execute(sql, data).fetchone()
        if row is None:
            return None, None
        i, detached = row
        return node_type(self, i, label), bool(detached)

    def node(self, node_type: type[NodeType], i: int) -> NodeType | None:
        """Return the node for the given node class and label or index."""
        sql = "SELECT kind, label FROM node WHERE i = ?"
        data = (i,)
        row = self._con.execute(sql, data).fetchone()
        if row is None:
            return None
        kind, label = row
        if kind != node_type.kind():
            raise TypeError(f"Node with id {i} is not of type {node_type.kind()}")
        return node_type(self, i, label)

    def nodes(
        self,
        node_type: type[NodeType] = Node,
        include_detached: bool = False,
    ) -> Iterator[NodeType]:
        """Iterate over all nodes of a certain kind."""
        query = "SELECT i, kind, label FROM node"
        data = []
        words = ["WHERE", "AND"]
        if node_type is not Node:
            query += f" {words.pop(0)} kind = ?"
            data.append(node_type.kind())
        if not include_detached:
            query += f" {words.pop(0)} NOT detached"
        for i, kind, label in self._con.execute(query, data):
            yield self._node_classes[kind](self, i, label)

    def walk_consumers(self, initial_is: Iterable[int]) -> Iterator[int]:
        """Iterate over all identifiers of indirect consumers of this node."""
        self.con.execute(DROP_CONSUMERS)
        self.con.execute(INITIAL_CONSUMERS)
        try:
            self.con.executemany(
                "INSERT OR IGNORE INTO temp.initial_consumer VALUES(?)", ((i,) for i in initial_is)
            )
            for row in self.con.execute(RECURSE_CONSUMERS + SELECT_WALK):
                yield row[0]
        finally:
            self.con.execute(DROP_CONSUMERS)

    #
    # Formatting
    #

    def format_str(self) -> str:
        """Return a multi-line string representation of the graph."""
        lines = []
        cur = self._con.execute(
            "SELECT node.i, node.kind, node.label, node.detached, "
            "cnode.i, cnode.kind, cnode.label, cnode.detached "
            "FROM node LEFT JOIN node as cnode ON node.creator = cnode.i"
        )
        for i, kind, label, detached, ci, ckind, clabel, cdetached in cur:
            node = self._node_classes[kind](self, i, label)
            creator = None if ci is None else self._node_classes[ckind](self, ci, clabel)
            lines.append(node.key(detached))
            for name, value in node.format_properties():
                lines.append(f"{name:>20s} = {value!s}")
            pairs = []
            if ci is not None and (label != clabel):
                pairs.append(("created by", creator.key(cdetached)))
            pairs.extend(("consumes", other_str) for _, other_str in node.suppliers_str())
            pairs.extend(
                ("creates", other_str) for other_str in node.products_str() if other_str != "root:"
            )
            pairs.extend(("supplies", other_str) for _, other_str in node.consumers_str())
            for role, key in pairs:
                lines.append(f"{role:>20s}   {key}")
            lines.append("")
        return "\n".join(lines)

    #
    # Graph modifications
    #

    def _check_creator(self, node_type: type[Node], creator: Node | None) -> None:
        """Validate the creator before a node is created. Override in subclasses."""
        if node_type is Root and self._con.execute("SELECT count(*) FROM node").fetchone()[0] > 0:
            raise GraphError("Only one root node is allowed and it must be the first node.")

    def _check_supplier(self, supplier: Node, consumer: Node) -> None:
        """Validate a supplier-consumer edge before it is inserted. Override in subclasses."""

    def create(
        self, node_type: type[NodeType], creator: Node | None, label: str = "", **kwargs
    ) -> NodeType:
        """Add a newly created node with reference to its creator, if any.

        Parameters
        ----------
        node_type
            Subclass of Node.
        creator
            The node that created the new node.
            Set to None to create a detached node.
        label
            The label of the node.
        kwargs
            Additional node-specific arguments used to initialize the node in the database.

        Returns
        -------
        new_node
            The newly created node.
        """
        # Sanity checking
        if not isinstance(node_type, type):
            raise TypeError(f"Argument node_type must be a type, got {node_type}")
        if not issubclass(node_type, Node):
            raise TypeError(f"Argument node_type must be a subclass of Node, got {node_type}")
        if not (isinstance(creator, Node) or creator is None):
            raise TypeError(f"Argument creator must be a Node or None, got {type(creator)}")
        self._check_creator(node_type, creator)
        label = node_type.create_label(label, **kwargs)

        node, detached = self.find_detached(node_type, label)
        if node is not None:
            # Recycle old data if needed and add/update node
            if not detached:
                raise GraphError(f"Node ({node.key()}) already exists and is not detached.")

            # Get the old creator before this information is lost.
            old_creator, old_creator_detached = node.creator_detached()
            # Replace the old creator by the new one.
            if creator is None:
                creator_i = None
                detached = True
            else:
                creator_i = creator.i
                detached = creator.is_detached()
            self._con.execute(
                "UPDATE node SET creator = ?, detached = ? WHERE i = ?",
                (creator_i, detached, node.i),
            )
            # Clean up the old creator (if any) that it lost a product.
            if old_creator is not None:
                if not old_creator_detached:
                    raise GraphError("Old creator of detached node is not detached.")
                old_creator.give_up()
            # Cut all ties to suppliers, so this node starts from a clean slate.
            node.del_suppliers()
            # Since this node is recreated, it cannot have created other nodes (yet).
            for product in node.products():
                product.detach()
        else:
            detached = True if creator is None else creator.is_detached()
            # Add new node
            cur = self._con.execute(
                "INSERT INTO node (kind, label, creator, detached) VALUES (?, ?, ?, ?)",
                (node_type.kind(), label, None if creator is None else creator.i, detached),
            )
            node_i = cur.lastrowid
            if node_type is Root:
                self._con.execute(
                    "UPDATE node SET creator = ?, detached = FALSE WHERE i = ?", (node_i, node_i)
                )
            node = node_type(self, node_i, label)
        node.initialize(**kwargs)
        node.validate()
        return node

    def clean(self):
        """Delete all detached nodes that can be removed safely."""
        cleaned_some = True
        while cleaned_some:
            cleaned_some = False
            # Look for detached nodes without consumers or products.
            # As long nodes have consumers or products, they cannot be removed.
            query = (
                "SELECT i, kind, label FROM node WHERE detached AND "
                "NOT EXISTS (SELECT 1 FROM node AS cnode WHERE node.i = cnode.creator) AND "
                "NOT EXISTS (SELECT 1 FROM dependency WHERE node.i = dependency.supplier)"
            )
            for i, kind, label in self._con.execute(query):
                node = self._node_classes[kind](self, i, label)
                cleaned_some = True
                node.del_suppliers()
                node.clean()
                self._con.execute("DELETE FROM node where i = ?", (i,))
