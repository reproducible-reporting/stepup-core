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
"""StepUp's abstract implementation of the provenance and dependency graphs."""

import inspect
import logging
import sqlite3
import sys
from collections.abc import Iterable, Iterator
from typing import Self

import attrs

from .exceptions import CyclicError, GraphError

__all__ = ("Cascade", "Node", "Root")


logger = logging.getLogger(__name__)


DROP_TABLES = """
PRAGMA foreign_keys=OFF;
SELECT 'DROP TABLE IF EXISTS "' || name || '";' FROM sqlite_master WHERE type = 'table';
PRAGMA foreign_keys=ON;
"""

CASCADE_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA application_id={application_id};
PRAGMA user_version={schema_version};
PRAGMA synchronous=OFF;

CREATE TABLE IF NOT EXISTS node (
    i INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    creator INTEGER,
    orphan BOOLEAN NOT NULL DEFAULT FALSE CHECK (orphan IN (FALSE, TRUE)),
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

RECURSE_ORPHAN = """
WITH RECURSIVE all_orphan(current, kind, label) AS (
    -- Initial: Select first generation of products
    SELECT i AS current, kind, label
    FROM node WHERE creator = ?
    UNION
    -- Recursion: Follow creator edges by selecting products of current
    SELECT node.i AS current, node.kind, node.label
    FROM node INNER JOIN all_orphan ON creator = current
)
UPDATE node SET orphan = ? WHERE i IN (SELECT current FROM all_orphan)
RETURNING i, kind, label
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
SELECT EXISTS (SELECT 1 FROM all_consumer WHERE current = ?) -- supplier
"""

SELECT_WALK = """
-- Final: Get all (indirect) consumers of a node.
SELECT current FROM all_consumer
"""

DROP_CONSUMERS = "DROP TABLE IF EXISTS temp.initial_consumer"


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
    - `update_orphan` to make changes when a node lost its creator node.
    - `detach` is called when an orphaned node must be cleaned up because it loses a product node.
    - `clean` to decide if an orphan node can be removed and to release resources.
    """

    cascade: "Cascade" = attrs.field(repr=False)
    i: int = attrs.field()
    label: str = attrs.field()

    @property
    def con(self) -> sqlite3.Connection:
        """The SQLite database."""
        return self.cascade.con

    @classmethod
    def kind(cls) -> str:
        """Lower-case prefix of the key string representing a node."""
        return cls.__name__.lower()

    def key(self, orphan: bool = False) -> str:
        """Return the key representation of the node."""
        result = f"{self.kind()}:{self.label}"
        if orphan:
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
        """Validate extra information about this node is present in the database."""

    def format_properties(self) -> Iterator[tuple[str, str]]:
        """Iterate over key-value pairs that represent the properties of the node."""
        yield from []

    def _make_orphan(self):
        """Update the node or the graph when it becomes orphan."""

    def _undo_orphan(self):
        """Update the node or the graph when the node is recreated."""

    def detach(self):
        """Clean up an orphaned node because it loses a product node.

        Implementations in subclasses should remove all related info,
        all edges, and make sure the node is deleted or orphaned.
        """
        raise NotImplementedError

    def clean(self):
        """Perform a cleanup right before the orphaned node is removed from the graph."""

    #
    # Getters and Iterators
    #

    def is_alive(self) -> bool:
        """True when the node is still present in the database."""
        return self.con.execute("SELECT 1 FROM node WHERE i = ?", (self.i,)).fetchone() is not None

    def is_orphan(self) -> bool:
        """True when the node or its creator (recursively) lost its creator node."""
        row = self.con.execute("SELECT orphan FROM node WHERE i = ?", (self.i,)).fetchone()
        return bool(row[0])

    def creator(
        self, *, return_orphan: bool = False
    ) -> Self | None | tuple[Self, bool] | tuple[None, None]:
        """Return the creator of the node."""
        if return_orphan:
            row = self.con.execute(
                "SELECT i, kind, label, orphan "
                "FROM node WHERE i = (SELECT creator FROM node WHERE i = ?)",
                (self.i,),
            ).fetchone()
            if row is None:
                return None, None
            i, kind, label, is_orphan = row
            return self.cascade._node_classes[kind](self.cascade, i, label), is_orphan

        row = self.con.execute(
            "SELECT node.i, node.kind, node.label "
            "FROM node WHERE node.i = (SELECT creator FROM node WHERE i = ?)",
            (self.i,),
        ).fetchone()
        if row is None:
            return None
        i, kind, label = row
        return self.cascade._node_classes[kind](self.cascade, i, label)

    def products(
        self,
        kind: str | None = None,
        yield_str: bool = False,
    ) -> Iterator[Self]:
        """Iterate over (a subset of) products of this node."""
        query = "SELECT i, kind, label, orphan FROM node WHERE creator = ?"
        data = [self.i]
        if kind is not None:
            query += " AND kind = ?"
            data.append(kind)
        for i, kind1, label, is_orphan in self.con.execute(query, data):
            node = self.cascade.node_classes[kind1](self.cascade, i, label)
            yield node.key(is_orphan) if yield_str else node

    def _dependencies(
        self,
        kind: str | None = None,
        include_orphans: bool = False,
        yield_str: bool = False,
        do_suppliers: bool = True,
    ) -> Iterator[Self | tuple[int, str]]:
        sql = (
            "SELECT node.i, kind, label, orphan, dependency.i "
            "FROM node JOIN dependency ON node.i = "
        )
        if do_suppliers:
            sql += "supplier WHERE consumer = ?"
        else:
            sql += "consumer WHERE supplier = ?"
        data = [self.i]
        if kind is not None:
            sql += " AND kind = ?"
            data.append(kind)
        if not include_orphans:
            sql += " AND NOT orphan"
        for i, kind1, label, is_orphan, idep in self.cascade.con.execute(sql, data):
            node = self.cascade.node_classes[kind1](self.cascade, i, label)
            if yield_str:
                yield idep, node.key(is_orphan)
            else:
                yield node

    def suppliers(
        self, kind: str | None = None, include_orphans: bool = False, yield_str: bool = False
    ) -> Iterator[Self | tuple[int, str]]:
        """Iterate over nodes that supply to this one."""
        yield from self._dependencies(kind, include_orphans, yield_str, do_suppliers=True)

    def consumers(
        self, kind: str | None = None, include_orphans: bool = False, yield_str: bool = False
    ) -> Iterator[Self | tuple[int, str]]:
        """Iterate over nodes that consume from this one."""
        yield from self._dependencies(kind, include_orphans, yield_str, do_suppliers=False)

    #
    # Graph modifications
    #

    def orphan(self):
        """Mark node as no longer being used, disconnect from its creator node.

        Orphaned nodes will have their creator set to NULL in the database.
        Actual deletion may take place when calling the clean method.

        When a node is orphaned, its `orphan` field in the node table is set to `True`,
        and this property is propagated recursively to all its product nodes.
        """
        row = self.con.execute("SELECT creator, orphan FROM node WHERE i = ?", (self.i,)).fetchone()
        if row is None:
            raise ValueError(f"Node id not in database: {self.i}")
        creator_i, is_orphan = row
        if creator_i is not None:
            self.con.execute("UPDATE node SET creator = NULL, orphan = TRUE WHERE i = ?", (self.i,))
            if not is_orphan:
                self._make_orphan()
                # Propagate the orphan=TRUE property to all product nodes.
                for i, kind, label in self.con.execute(RECURSE_ORPHAN, (self.i, True)):
                    node = self.cascade.node_classes[kind](self.cascade, i, label)
                    node._make_orphan()

    def recreate(self, new_creator: Self):
        """Reconnect the node to a new creator node.

        This method is used to reattach an orphaned node to a new creator node.
        It can only be called when the node has no (orphaned) creator.
        """
        old_creator = self.creator()
        if old_creator is not None:
            raise ValueError(
                "Node.recreate can only be called on a top-level orphaned node. "
                f"It's creator is {old_creator}."
            )
        self.con.execute(
            "UPDATE node SET creator = ?, orphan = FALSE WHERE i = ?", (new_creator.i, self.i)
        )
        self._undo_orphan()
        # Propagate the orphan=FALSE property to all product nodes.
        for i, kind, label in self.con.execute(RECURSE_ORPHAN, (self.i, False)):
            node = self.cascade.node_classes[kind](self.cascade, i, label)
            node._undo_orphan()

    def add_supplier(self, supplier: Self) -> int:
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


@attrs.define
class Root(Node):
    """The root node of the provenance and dependency graph.

    (Indirect) products of the root node are considered active nodes in the graph.
    Nodes that are not connected (indirectly) to the root node are considered orphaned,
    and will be removed when the Cascade.clean method is called.
    """

    def update_orphan(self):
        """This method is called when the creator of a node is set NULL."""
        raise AssertionError("Root node cannot be orphaned")

    def clean(self):
        """Perform a cleanup right before the orphaned node is removed from the graph."""
        raise AssertionError("Root node cannot be cleaned")

    def detach(self):
        """Clean up an orphaned node because it loses a product node."""
        raise AssertionError("Root node cannot be detached because that would mean it is orphaned.")


@attrs.define
class ConnectionWrapper:
    """Wrapper for SQLite connection that prints from where execute is called."""

    _con: sqlite3.Connection = attrs.field()
    _explain: bool = attrs.field(default=False)

    def _print_caller(self, name: str):
        frame = inspect.currentframe().f_back.f_back
        module = frame.f_globals.get("__name__", "Unknown Module")
        lineno = frame.f_lineno
        print(f"sqlite3 {name} called from {module}:{lineno}", file=sys.stderr)

    def execute(self, sql: str, data: tuple = ()):
        self._print_caller("execute")
        if self._explain:
            print(sql, file=sys.stderr)
            nodes = {}
            from anytree import Node, RenderTree

            for step in self._con.execute(f"EXPLAIN QUERY PLAN {sql}", data):
                node_id, parent_id, _, detail = step
                nodes[node_id] = Node(f"({node_id}) {detail}", parent=nodes.get(parent_id))
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
        return 768739001

    @property
    def schema_version(self) -> int:
        return 1

    @classmethod
    def schema(cls) -> str:
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
            file_version = self._con.execute("PRAGMA user_version").fetchone()[0]
            if file_version != self.schema_version:
                self._con.executescript(DROP_TABLES)
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
            self._root = self.create("root", None)
        else:
            self._root = self.find("root", "")
            self.check_consistency()

    def check_consistency(self):
        """Check whether the graph satisfies all constraints."""
        if self._root.creator() != self._root:
            raise GraphError("Invalid cascade: root node does not create itself")
        if self._root.is_orphan():
            raise GraphError("Invalid cascade: root node cannot be orphan")
        sql = (
            "SELECT node.i, node.kind, node.label, node.creator, node.orphan, cnode.orphan FROM "
            "node LEFT JOIN node AS cnode ON node.creator = cnode.i"
        )
        for row in self.con.execute(sql):
            i, kind, label, creator_i, is_orphan, corphan = row
            if i > 1 and creator_i == i:
                node = self._node_classes[kind](self, i, label)
                raise GraphError(f"Non-root node is its own creator: {node.key()}")
            creator_is_orphan = creator_i is None or corphan
            if is_orphan:
                if not creator_is_orphan:
                    node = self._node_classes[kind](self, i, label)
                    raise GraphError(f"Orphaned node has non-orphan creator: {node.key()}")
            elif creator_is_orphan:
                node = self._node_classes[kind](self, i, label)
                raise GraphError(f"Non-orphan created orphan node: {node.key()}")
        for node in self.nodes():
            node.validate()

    #
    # Basic attributes and introspection
    #

    @property
    def con(self) -> sqlite3.Connection:
        # return self._con_wrapper
        return self._con

    @property
    def root(self) -> Root:
        return self._root

    def find(
        self, kind: str, label: str, *, return_orphan=False
    ) -> Node | None | tuple[Node, bool] | tuple[None, None]:
        """Return the node ID for the given node class and label."""
        if return_orphan:
            sql = "SELECT i, orphan FROM node WHERE kind = ? AND label = ?"
            data = (kind, label)
            row = self._con.execute(sql, data).fetchone()
            if row is None:
                return None, None
            i, is_orphan = row
            return (
                self._node_classes[kind](self, i, label),
                bool(is_orphan),
            )

        sql = "SELECT i FROM node WHERE kind = ? AND label = ?"
        data = (kind, label)
        row = self._con.execute(sql, data).fetchone()
        return None if row is None else self._node_classes[kind](self, row[0], label)

    def nodes(
        self,
        kind: str | None = None,
        include_orphans: bool = False,
    ) -> Iterator[Node]:
        """Return all nodes of a certain kind."""
        query = "SELECT i, kind, label FROM node"
        data = []
        words = ["WHERE", "AND"]
        if kind is not None:
            query += f" {words.pop(0)} kind = ?"
            data.append(kind)
        if not include_orphans:
            query += f" {words.pop(0)} NOT orphan"
        for i, kind1, label in self._con.execute(query, data):
            yield self._node_classes[kind1](self, i, label)

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
            "SELECT node.i, node.kind, node.label, node.orphan, "
            "cnode.i, cnode.kind, cnode.label, cnode.orphan "
            "FROM node LEFT JOIN node as cnode ON node.creator = cnode.i"
        )
        for i, kind, label, is_orphan, ci, ckind, clabel, cis_orphan in cur:
            node = self._node_classes[kind](self, i, label)
            creator = None if ci is None else self._node_classes[ckind](self, ci, clabel)
            lines.append(node.key(is_orphan))
            for name, value in node.format_properties():
                lines.append(f"{name:>20s} = {value!s}")
            pairs = []
            if ci is not None and (label != clabel):
                pairs.append(("created by", creator.key(cis_orphan)))
            pairs.extend(
                ("consumes", other_str)
                for _, other_str in sorted(
                    node.suppliers(include_orphans=True, yield_str=True), key=(lambda x: x[1])
                )
            )
            pairs.extend(
                ("creates", other_str)
                for other_str in sorted(node.products(yield_str=True))
                if other_str != "root:"
            )
            pairs.extend(
                ("supplies", other_str)
                for _, other_str in sorted(
                    node.consumers(include_orphans=True, yield_str=True), key=(lambda x: x[1])
                )
            )
            for role, key in pairs:
                lines.append(f"{role:>20s}   {key}")
            lines.append("")
        return "\n".join(lines)

    #
    # Graph modifications
    #

    def create(self, kind: str, creator: Node | None, label: str = "", **kwargs) -> Node:
        """Add a newly created node with reference to its creator, if any.

        Parameters
        ----------
        kind
            String identifier for the Node subclass.
        creator
            The node that created the new node.
            Set to None to create an orphan node.
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
        if not isinstance(kind, str):
            raise TypeError(f"Argument kind must be a string, got {kind}")
        node_class = self._node_classes.get(kind)
        if node_class is None:
            raise TypeError(f"Unregistered node kind: {kind}")
        if not (isinstance(creator, Node) or creator is None):
            raise TypeError(f"Argument creator must be a Node or None, got {type(creator)}")
        label = node_class.create_label(label, **kwargs)

        node, is_orphan = self.find(kind, label, return_orphan=True)
        if node is not None:
            # Recycle old data if needed and add/update node
            if not is_orphan:
                raise GraphError(f"Node ({node.key()}) already exists and is not orphan.")

            # Get the old creator before this information is lost.
            old_creator, old_creator_is_orphan = node.creator(return_orphan=True)
            # Replace the old creator by the new one.
            if creator is None:
                creator_i = None
                is_orphan = True
            else:
                creator_i = creator.i
                is_orphan = creator.is_orphan()
            self._con.execute(
                "UPDATE node SET creator = ?, orphan = ? WHERE i = ?",
                (creator_i, is_orphan, node.i),
            )
            # Clean up the old creator (if any) that it lost a product.
            if old_creator is not None and old_creator_is_orphan:
                old_creator.detach()
            # Cut all ties to suppliers, so this node starts from a clean slate.
            node.del_suppliers()
            # Since this node is recreated, it cannot have created other nodes (yet).
            for product in node.products():
                product.orphan()
        else:
            is_orphan = True if creator is None else creator.is_orphan()
            # Add new node
            cur = self._con.execute(
                "INSERT INTO node (kind, label, creator, orphan) VALUES (?, ?, ?, ?)",
                (kind, label, None if creator is None else creator.i, is_orphan),
            )
            node_i = cur.lastrowid
            if kind == "root":
                if self._con.execute("SELECT count(*) FROM node").fetchone()[0] > 1:
                    raise GraphError("Only one root node is allowed and it must be the first node.")
                self._con.execute(
                    "UPDATE node SET creator = ?, orphan = FALSE WHERE i = ?", (node_i, node_i)
                )
            node = node_class(self, node_i, label)
        node.initialize(**kwargs)
        node.validate()
        return node

    def clean(self):
        """Delete all orphaned nodes that can be removed safely."""
        cleaned_some = True
        while cleaned_some:
            cleaned_some = False
            # Look for orphans without consumers or products.
            # As long is nodes have consumers or products, they cannot be removed.
            query = (
                "SELECT i, kind, label FROM node WHERE orphan = TRUE AND "
                "NOT EXISTS (SELECT 1 FROM node AS cnode WHERE node.i = cnode.creator) AND "
                "NOT EXISTS (SELECT 1 FROM dependency WHERE node.i = dependency.supplier)"
            )
            for i, kind, label in self._con.execute(query):
                node = self._node_classes[kind](self, i, label)
                cleaned_some = True
                node.clean()
                node.del_suppliers()
                self._con.execute("DELETE FROM node where i = ?", (i,))
