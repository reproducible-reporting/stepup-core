# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""StepUp's abstract implementation of the provenance and dependency graphs."""

import logging
import sqlite3
from collections.abc import Iterable, Iterator
from typing import Self, TypeVar

import attrs

from .exceptions import ConsistencyError, CyclicError, GraphError
from .sqlite3 import DBSession

__all__ = ("Node", "NodeType", "Root", "Trellis")


logger = logging.getLogger(__name__)


# The SQL schema for the Trellis database, consists of two main parts:
# 1. The `node` table, which stores for each node its kind, label, creator and detached flag.
#    (This contains the creator -> product edges of the provenance graph.)
# 2. The `dependency` table, which stores all source-sink relations of the dependency graph.
TRELLIS_SCHEMA = """
PRAGMA application_id={application_id};
PRAGMA user_version={schema_version};

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
    -- This is kept up-to-date by the detach and recycle methods of Node,
    -- and also written directly by Trellis.create() when it reuses or creates a node row.
    -- Detached nodes are used for various purposes in StepUp:
    -- * To determine whether a node should be cleaned up.
    -- * To exclude detached steps from scheduling.
    -- * To keep metadata about detached nodes in case they are recycled later.
    FOREIGN KEY (creator) REFERENCES node(i),
    -- The root node is always node 1, is its own creator, is never detached,
    -- and has an empty label. Non-root nodes have none of these properties.
    CHECK (kind = 'root' OR i != 1),
    CHECK (kind = 'root' OR creator IS NOT NULL OR detached),
    CHECK (kind = 'root' OR creator IS NULL OR creator != i),
    CHECK (kind != 'root' OR i = 1),
    CHECK (kind != 'root' OR creator IS i),
    CHECK (kind != 'root' OR NOT detached),
    CHECK (kind != 'root' OR label = '')
);
CREATE INDEX IF NOT EXISTS node_creator_kind ON node (creator, kind);
CREATE UNIQUE INDEX IF NOT EXISTS node_kind_label ON node (kind, label);
CREATE INDEX IF NOT EXISTS node_detached ON node (i) WHERE detached;

CREATE TABLE IF NOT EXISTS dependency (
    i INTEGER PRIMARY KEY,
    source INTEGER NOT NULL,
    sink INTEGER NOT NULL,
    UNIQUE (source,sink),
    FOREIGN KEY (source) REFERENCES node(i),
    FOREIGN KEY (sink) REFERENCES node(i)
);
-- An index on (source, sink) is already provided by the UNIQUE(source, sink)
-- constraint above (sqlite_autoindex_dependency_1), so only the reverse direction is added here.
CREATE INDEX IF NOT EXISTS dependency_sink_source ON dependency(sink, source);
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

# Recursively find all nodes reachable from a given node by following creator -> product edges,
# including the given node itself, then report every node whose detached flag disagrees with
# that reachability. A purely local (single-hop) comparison of a node against its immediate
# creator cannot detect such a mismatch in the presence of a longer cycle in the creator chain
# (e.g. A creates B creates A), so this walks the full chain instead.
# A node is consistent when it is detached and unreachable, or attached and reachable.
# The returned detached flag doubles as the inconsistency kind:
# TRUE means "detached but reachable", FALSE means "attached but unreachable".
CHECK_DETACHED_REACHABILITY = """
WITH RECURSIVE all_products(current) AS (
    -- Initial: Set initial node
    SELECT ? AS current
    UNION
    -- Recursion: Follow creator -> product edges by selecting products of current
    SELECT node.i AS current
    FROM node INNER JOIN all_products ON creator = current
)
SELECT node.i, node.kind, node.label, node.detached
FROM node LEFT JOIN all_products ON node.i = all_products.current
WHERE node.detached = (all_products.current IS NOT NULL)
"""

# Recursively find all sinks of a node by following source -> sink edges,
# including the given node itself.
# This can be combined with either SELECT_ALL_SINKS or SELECT_CYCLIC.
RECURSE_SINKS = """
WITH RECURSIVE all_sink(current) AS (
    -- Initial: Set initial node
    SELECT ? AS current
    UNION
    -- Recursion: Follow edges by selecting sinks of current
    SELECT sink AS current
    FROM dependency INNER JOIN all_sink ON source = current
)
"""

SELECT_ALL_SINKS = """
-- Final: Get all (indirect) sinks of a node.
SELECT current FROM all_sink
"""

SELECT_CYCLIC = """
-- Final: Check if any of the (indirect) sink matches the source in the new edge
SELECT EXISTS (SELECT 1 FROM all_sink WHERE current = ?)
"""


NodeType = TypeVar("NodeType", bound="Node")


@attrs.define(frozen=True)
class Node:
    """Base class for nodes in the provenance and dependency graphs.

    Instances of this object are merely references to information in the database.
    These are typically short-lived objects.
    They only store a few immutable pieces of information:

    - `graph`: the graph to which this node belongs.
    - `i`: the identifier of the node in the database.
    - `label`: a unique (within its kind) label for the node.

    All other information related to this node is directly taken from or stored in the database.

    Subclasses may override the following:

    - `kind` to control the formatting of the key string.
    - `schema` to extend the trellis schema.
    - `adjust_label` to override the user-provided label of a node.
    - `initialize_row` to create or update rows for new nodes outside the default Trellis tables.
    - `validate_row` to check if the necessary rows outside the default Trellis tables are made.
    - `format_properties` to define the properties of the node.
    - `before_delete` to release resources related to a node.
    - `after_lost_product` is called after a detached node loses a product node.
    - `can_recycle` to decide if a detached node can be fully recycled by `Trellis.try_recycle`.
    - `after_recycle` to update the mutable declared properties after a full recycle.
    """

    #
    # Immutable attributes
    #

    graph: "Trellis" = attrs.field(repr=False)
    """The Trellis object that contains the node."""

    i: int = attrs.field()
    """The identifier of the node in the database."""

    label: str = attrs.field()
    """The label of the node.

    While this can be derived from the database, it is stored here for convenience,
    since it is considered immutable.
    """

    #
    # Methods to be overridden by subclasses
    #

    @classmethod
    def kind(cls) -> str:
        """Lower-case prefix of the key string representing a node.

        This string is the node's type discriminator in the `node.kind` column,
        and the key under which `Trellis` looks up the class for a stored row.
        Overriding it in an existing subclass changes the database schema.

        Code holding a `Node` instance should branch with `isinstance`, not on this string;
        the string is for rendering and for code that reads the database
        without constructing nodes.
        """
        return cls.__name__.lower()

    @classmethod
    def schema(cls) -> str | None:
        """Return node-specific SQL commands to initialize the database."""
        return None

    @classmethod
    def adjust_label(cls, label: str, **kwargs):
        """Optionally override the user-provided label when creating a node."""
        return str(label)

    def initialize_row(self, **kwargs):
        """Create extra information in the database about this node."""

    def validate_row(self):
        """Validate that extra information about this node is present in the database."""

    def format_properties(self) -> Iterator[tuple[str, str]]:
        """Iterate over key-value pairs with the properties of the node, for terminal display."""
        yield from []

    def before_delete(self):
        """Perform a cleanup right before the detached node is deleted from the graph."""

    def after_lost_product(self):
        """Invalidate cached results because a product of this detached node was removed.

        This keeps the node (and its recyclable metadata) in the graph:
        only `Trellis.delete_detached` ever deletes nodes.
        It is called on a detached node that loses a product,
        either because the product was deleted (`Trellis.delete_detached`)
        or because another creator took it over (`Trellis.create` and `Node.reattach`).
        Such a node is no longer a faithful record of what it created,
        so subclasses must drop whatever would let them be skipped when recycled later.

        The default implementation does nothing,
        which is correct for nodes that store no such result.
        """

    def can_recycle(self, **kwargs) -> bool:
        """Decide whether this detached node may be fully recycled by `Trellis.try_recycle`.

        A full recycle keeps the node's edges, products and satellite data,
        so it is only allowed when the given arguments are compatible
        with the information already stored for this node.

        The default implementation is to never recycle fully, which subclasses can override.

        Callers that want full recycling should call `Trellis.try_recycle` first
        and fall back to `Trellis.create`, which reuses only the node row,
        when it returns `None`.
        """
        return False

    def after_recycle(self, **kwargs):
        """Update the mutable declared properties of this node after a full recycle."""

    #
    # Properties, Getters and Iterators
    #

    @property
    def db(self) -> DBSession:
        """The SQLite database."""
        return self.graph.db

    def key(self, detached: bool = False) -> str:
        """Return the key representation of the node, for terminal display."""
        result = f"{self.kind()}:{self.label}"
        if detached:
            result = f"({result})"
        return result

    def in_graph(self) -> bool:
        """True when the node is still present in the database."""
        return self.db.execute("SELECT 1 FROM node WHERE i = ?", (self.i,)).fetchone() is not None

    def is_detached(self) -> bool:
        """True when the node or its creator (recursively) lost its creator node."""
        row = self.db.execute("SELECT detached FROM node WHERE i = ?", (self.i,)).fetchone()
        return bool(row[0])

    def creator(self) -> Self | None:
        """Return the creator of the node."""
        row = self.db.execute(
            "SELECT node.i, node.kind, node.label "
            "FROM node WHERE node.i = (SELECT creator FROM node WHERE i = ?)",
            (self.i,),
        ).fetchone()
        if row is None:
            return None
        i, kind, label = row
        return self.graph.node_from_row(i, kind, label)

    def creator_and_detached(self) -> tuple[Self, bool] | tuple[None, None]:
        """Return the creator of the node.

        Returns
        -------
        creator
            The creator node, or `None` if there is no creator.
        detached
            Whether the creator node is detached.
        """
        row = self.db.execute(
            "SELECT i, kind, label, detached "
            "FROM node WHERE i = (SELECT creator FROM node WHERE i = ?)",
            (self.i,),
        ).fetchone()
        if row is None:
            return None, None
        i, kind, label, detached = row
        return self.graph.node_from_row(i, kind, label), detached

    def products(self, node_type: type[NodeType] | None = None) -> Iterator[NodeType]:
        """Iterate over the products of this node, optionally filtered by node type."""
        query = "SELECT i, kind, label FROM node WHERE creator = ?"
        data = [self.i]
        if node_type is not None:
            query += " AND kind = ?"
            data.append(node_type.kind())
        for i, kind, label in self.db.execute(query, data):
            yield self.graph.node_from_row(i, kind, label)

    def _node_keys(self, sql: str, node_type: type[NodeType] | None = None) -> Iterator[str]:
        """Iterate over the nodes selected by a partial query, formatted as `kind:label` keys.

        Parameters
        ----------
        sql
            A query selecting `kind, label, detached` from `node`,
            ending in a `WHERE` clause with one placeholder bound to this node's id.
        node_type
            When given, only nodes of this type are included.

        Returns
        -------
        keys
            The `kind:label` key of every selected node, ordered by kind and label,
            with the keys of detached nodes wrapped in parentheses.
        """
        data = [self.i]
        if node_type is not None:
            sql += " AND kind = ?"
            data.append(node_type.kind())
        sql += " ORDER BY kind, label"
        for kind, label, detached in self.db.execute(sql, data):
            key = f"{kind}:{label}"
            if detached:
                key = f"({key})"
            yield key

    def product_keys(self, node_type: type[NodeType] | None = None) -> Iterator[str]:
        """Iterate over the products of this node, formatted as `kind:label` keys."""
        yield from self._node_keys(
            "SELECT kind, label, detached FROM node WHERE creator = ?", node_type
        )

    def _dependencies(
        self,
        node_type: type[NodeType] | None = None,
        include_detached: bool = False,
        *,
        upstream: bool = True,
    ) -> Iterator[NodeType]:
        sql = "SELECT node.i, kind, label FROM node JOIN dependency ON node.i = " + (
            "source WHERE sink = ?" if upstream else "sink WHERE source = ?"
        )
        data = [self.i]
        if node_type is not None:
            sql += " AND kind = ?"
            data.append(node_type.kind())
        if not include_detached:
            sql += " AND NOT detached"
        for i, kind, label in self.db.execute(sql, data):
            yield self.graph.node_from_row(i, kind, label)

    def sources(
        self, node_type: type[NodeType] | None = None, include_detached: bool = False
    ) -> Iterator[NodeType]:
        """Iterate over nodes that supply to this one."""
        yield from self._dependencies(node_type, include_detached, upstream=True)

    def sinks(
        self, node_type: type[NodeType] | None = None, include_detached: bool = False
    ) -> Iterator[NodeType]:
        """Iterate over nodes that consume from this one."""
        yield from self._dependencies(node_type, include_detached, upstream=False)

    def _dependency_keys(
        self,
        node_type: type[NodeType] | None = None,
        *,
        upstream: bool,
    ) -> Iterator[str]:
        """Iterate over sinks (or with reverse=True sources), formatted as `kind:label` keys.

        Detached edges are wrapped in parentheses.
        Subclasses may decorate the strings with additional information.
        """
        yield from self._node_keys(
            "SELECT kind, label, detached FROM node JOIN dependency ON node.i = "
            + ("source WHERE sink = ?" if upstream else "sink WHERE source = ?"),
            node_type,
        )

    def source_keys(self, node_type: type[NodeType] | None = None) -> Iterator[str]:
        """Iterate over nodes that supply to this one, formatted as `kind:label` keys."""
        yield from self._dependency_keys(node_type, upstream=True)

    def sink_keys(self, node_type: type[NodeType] | None = None) -> Iterator[str]:
        """Iterate over nodes that consume from this one, formatted as `kind:label` keys."""
        yield from self._dependency_keys(node_type, upstream=False)

    #
    # Graph modifications
    #

    def detach(self):
        """Mark node as no longer being created, disconnect from its creator node.

        Detached nodes will have their creator set to NULL in the database.
        Actual deletion may take place when calling the `delete_detached` method.

        When a node is detached, its `detached` field in the node table is set to `True`,
        and this property is propagated recursively to all its product nodes.

        Raises
        ------
        ValueError
            If the node is not found in the database.
        """
        row = self.db.execute(
            "SELECT creator, detached FROM node WHERE i = ?", (self.i,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Node id not in database: {self.i}")
        creator_i, detached = row
        if creator_i is not None:
            self.db.execute(
                "UPDATE node SET creator = NULL, detached = TRUE WHERE i = ?", (self.i,)
            )
            if not detached:
                self.db.execute(RECURSIVELY_SET_DETACHED, (self.i, True))

    def reattach(self, new_creator: "Node"):
        """Reattach the node to a new creator node, preserving its properties.

        The node inherits the new creator's `detached` flag, as in `Trellis.create`:
        a detached creator only ever creates detached products.
        Reattaching to a detached creator therefore re-parents the node without reviving it.

        Raises
        ------
        ValueError
            If the node is not detached.
        TypeError
            If the new_creator is not an instance of Node.
        """
        if not self.is_detached():
            raise ValueError("Node.reattach can only be called on a detached node.")
        if not isinstance(new_creator, Node):
            raise TypeError(f"Argument new_creator must be a Node, got {type(new_creator)}")
        detached = new_creator.is_detached()
        old_creator, old_creator_detached = self.creator_and_detached()
        self.db.execute(
            "UPDATE node SET creator = ?, detached = ? WHERE i = ?",
            (new_creator.i, detached, self.i),
        )
        # The old creator, if any, no longer records everything it created.
        # It is left in the graph (it may still be recycled), but not as a skippable node.
        if old_creator is not None:
            if not old_creator_detached:
                raise ConsistencyError("Old creator of detached node is not detached.")
            old_creator.after_lost_product()
        # Propagate the inherited detached property to all product nodes.
        self.db.execute(RECURSIVELY_SET_DETACHED, (self.i, detached))

    def check_sources_acyclic(self, source_is: Iterable[int]) -> None:
        """Verify that several new source edges can be added without introducing a cycle.

        This computes the set of (indirect) sinks of this node once,
        and checks all candidate source identifiers against it.
        It must only be used when this node is the *sink* of every candidate edge
        in the batch, since adding such edges cannot change what this node can reach
        going forward.

        Parameters
        ----------
        source_is
            Identifiers of candidate source nodes that would each become
            a new source of this node.

        Raises
        ------
        CyclicError
            If any of the given identifiers is an (indirect) sink of this node
            (or is this node itself), which means the corresponding edge would
            introduce a cyclic dependency.
        """
        cur = self.db.execute(RECURSE_SINKS + SELECT_ALL_SINKS, (self.i,))
        sink_is = {row[0] for row in cur}
        if not sink_is.isdisjoint(source_is):
            raise CyclicError("New relation introduces a cyclic dependency")

    def add_source(self, source: "Node", skip_cycle_check: bool = False) -> int:
        """Add a source-sink relation.

        Parameters
        ----------
        source
            Other node that supplies to this node.
        skip_cycle_check
            Skip the cyclic-dependency check.
            Only set this to `True` when the caller has already verified,
            e.g. via `check_sources_acyclic`, that this edge cannot introduce a cycle.

        Returns
        -------
        idep
            The identifier in the dependency table.

        Raises
        ------
        CyclicError
            If `skip_cycle_check` is `False` and the new edge would introduce
            a cyclic dependency.
        GraphError
            If the relation already exists.
        """
        if not skip_cycle_check:
            # Check whether the new edge would introduce a cyclic dependency.
            cur = self.db.execute(RECURSE_SINKS + SELECT_CYCLIC, (self.i, source.i))
            if cur.fetchone()[0] > 0:
                raise CyclicError("New relation introduces a cyclic dependency")
        try:
            cur = self.db.execute(
                "INSERT INTO dependency(source, sink) VALUES(?, ?)",
                (source.i, self.i),
            )
        except sqlite3.IntegrityError as exc:
            raise GraphError("Relation already exists") from exc
        return cur.lastrowid

    def del_sources(self, sources: list["Node"]):
        """Delete given sources."""
        self.db.executemany(
            "DELETE FROM dependency WHERE source = ? AND sink = ?",
            ((source.i, self.i) for source in sources),
        )

    def del_all_sources(self):
        """Delete all sources of the current node."""
        self.db.execute("DELETE FROM dependency WHERE sink = ?", (self.i,))


@attrs.define(frozen=True)
class Root(Node):
    """The root node of the provenance and dependency graph.

    (Indirect) products of the root node are considered active nodes in the graph.
    Nodes that are not connected (indirectly) to the root node are considered detached,
    and will be removed when the `Trellis.delete_detached()` method is called.
    """

    def before_delete(self):
        """Always raise, since the root node is never detached and thus never cleaned up."""
        raise AssertionError("Root node cannot be deleted")

    def after_lost_product(self):
        """Always raise, since the root node can never lose a product this way.

        `Node.after_lost_product()` is only called on a detached node:
        `Trellis.delete_detached()` only deletes detached nodes,
        and `Trellis.create()` and `Node.reattach()` only take a product away
        from an already-detached creator.
        The root node is never detached.
        """
        raise AssertionError("A node can never be detached from the Root node.")


@attrs.define(eq=False)
class Trellis:
    """Base class for combined provenance + dependency graphs."""

    db: DBSession = attrs.field()
    """The database session managing the SQLite connection where the graph is stored."""

    node_classes: dict[str, type[Node]] = attrs.field(init=False)
    """The types of nodes that are supported."""

    _root: Root = attrs.field(init=False)
    """Pre-fetched results from the database."""

    @node_classes.default
    def _default_node_classes(self) -> dict[str, type[Node]]:
        return {node_class.kind(): node_class for node_class in self.default_node_classes()}

    #
    # Initialization
    #

    @staticmethod
    def default_node_classes() -> list[type[Node]]:
        """Specify the default node classes that are supported by this graph.

        Subclasses can override this to add more node classes."""
        return [Root]

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
        # Schema 4 became outdated due to the v4.0.0 rewrite.
        return 5

    @classmethod
    def schema(cls) -> str:
        """Return the SQL schema for the database. (Does not include node-specific schemas.)"""
        return TRELLIS_SCHEMA

    async def initialize(self):
        """Initialize or check the initial database."""
        schema_blobs = [self.schema()]
        schema_blobs.extend(node_class.schema() for node_class in self.node_classes.values())
        empty = await self.db.initialize(self.application_id, self.schema_version, schema_blobs)
        async with self.db:
            if empty:
                self._root = self.create(Root, None)
            else:
                self._root = self.find(Root, "")
                self._rebuild_temp_tables()
                self._check_consistency()

    def _rebuild_temp_tables(self):
        """Rebuild scratch temp tables that need seeding once per fresh connection.

        A no-op in the base class.
        Subclasses that add trigger-maintained temp tables
        (which start empty on every fresh connection)
        override this to backfill them from the persistent tables they cache.
        """

    def _check_consistency(self):
        """Check whether the graph satisfies all constraints."""
        # Root-node facts (id 1, self-creating, never detached, empty label) and non-root
        # self-creation are enforced by CHECK constraints on the node table,
        # so no Python-side check is needed here.
        sql = (
            "SELECT node.i, node.kind, node.label, node.creator, node.detached, cnode.detached "
            "FROM node LEFT JOIN node AS cnode ON node.creator = cnode.i"
        )
        for row in self.db.execute(sql):
            i, kind, label, creator_i, detached, creator_detached = row
            creator_detached = creator_i is None or creator_detached
            if detached:
                if not creator_detached:
                    node = self.node_from_row(i, kind, label)
                    raise ConsistencyError(f"Detached node has attached creator: {node.key()}")
            elif creator_detached:
                node = self.node_from_row(i, kind, label)
                raise ConsistencyError(f"Attached node has detached creator: {node.key()}")
        # The per-row checks above only catch immediate (single-hop) inconsistencies between a
        # node and its own creator. A longer cycle in the creator chain (e.g. A creates B creates
        # A) can pass every one of those checks while never actually connecting to the root, so
        # also verify global reachability from the root along creator -> product edges.
        cur = self.db.execute(CHECK_DETACHED_REACHABILITY, (self._root.i,))
        row = cur.fetchone()
        if row is not None:
            i, kind, label, detached = row
            node = self.node_from_row(i, kind, label)
            if detached:
                raise ConsistencyError(
                    f"Detached node is reachable from root via creator chain: {node.key()}"
                )
            raise ConsistencyError(
                f"Attached node is not reachable from root via creator chain: {node.key()}"
            )
        for node in self.nodes():
            node.validate_row()

    #
    # Basic attributes and introspection
    #

    @property
    def root(self) -> Root:
        return self._root

    def node_from_row(self, i: int, kind: str, label: str) -> Node:
        """Construct a node object from the columns of a `node` table row.

        This performs no database access:
        it only selects the node class registered for `kind`.

        Parameters
        ----------
        i, kind, label
            The `i`, `kind` and `label` columns of the row,
            in the order in which they are selected throughout this module.

        Returns
        -------
        node
            An instance of the node class registered for the given kind.
        """
        return self.node_classes[kind](self, i, label)

    def find(self, node_type: type[NodeType], label: str) -> NodeType | None:
        """Return the node for the given node class and label."""
        sql = "SELECT i FROM node WHERE kind = ? AND label = ?"
        data = (node_type.kind(), label)
        row = self.db.execute(sql, data).fetchone()
        return None if row is None else node_type(self, row[0], label)

    def find_and_detached(
        self, node_type: type[NodeType], label: str
    ) -> tuple[NodeType, bool] | tuple[None, None]:
        """Return the node and detached flag for the given node class and label."""
        sql = "SELECT i, detached FROM node WHERE kind = ? AND label = ?"
        data = (node_type.kind(), label)
        row = self.db.execute(sql, data).fetchone()
        if row is None:
            return None, None
        i, detached = row
        return node_type(self, i, label), bool(detached)

    def nodes(
        self,
        node_type: type[NodeType] | None = None,
        include_detached: bool = False,
    ) -> Iterator[NodeType]:
        """Iterate over all nodes, optionally filtered by node type."""
        query = "SELECT i, kind, label FROM node"
        data = []
        words = ["WHERE", "AND"]
        if node_type is not None:
            query += f" {words.pop(0)} kind = ?"
            data.append(node_type.kind())
        if not include_detached:
            query += f" {words.pop(0)} NOT detached"
        for i, kind, label in self.db.execute(query, data):
            yield self.node_from_row(i, kind, label)

    #
    # Formatting
    #

    def format_str(self) -> str:
        """Return a multi-line string representation of the graph."""
        lines = []
        cur = self.db.execute(
            "SELECT node.i, node.kind, node.label, node.detached, "
            "cnode.i, cnode.kind, cnode.label, cnode.detached "
            "FROM node LEFT JOIN node as cnode ON node.creator = cnode.i"
        )
        for i, kind, label, detached, ci, ckind, clabel, cdetached in cur:
            node = self.node_from_row(i, kind, label)
            creator = None if ci is None else self.node_from_row(ci, ckind, clabel)
            lines.append(node.key(detached))
            for name, value in node.format_properties():
                lines.append(f"{name:>20s} = {value!s}")
            pairs = []
            if ci is not None and (label != clabel):
                pairs.append(("creator", creator.key(cdetached)))
            pairs.extend(("source", key) for key in node.source_keys())
            pairs.extend(("product", key) for key in node.product_keys() if key != "root:")
            pairs.extend(("sink", key) for key in node.sink_keys())
            for role, key in pairs:
                lines.append(f"{role:>20s}   {key}")
            lines.append("")
        return "\n".join(lines)

    #
    # Graph modifications
    #

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

        Raises
        ------
        ConsistencyError
            When an attached node with this label already exists.
            This is a last-resort guard: every collision a plan can cause should be caught upstream,
            where the node type is known and the message can name the two declarations that clash.
            If not caught upstream, and this error is raised, there is a bug in StepUp.
            The fix is to add the missing upstream message, not to soften this guard.
        """
        # Sanity checking
        if not isinstance(node_type, type):
            raise TypeError(f"Argument node_type must be a type, got {node_type}")
        if not issubclass(node_type, Node):
            raise TypeError(f"Argument node_type must be a subclass of Node, got {node_type}")
        if not (isinstance(creator, Node) or creator is None):
            raise TypeError(f"Argument creator must be a Node or None, got {type(creator)}")
        label = node_type.adjust_label(label, **kwargs)

        node, detached = self.find_and_detached(node_type, label)
        if node is not None:
            # Recycle old data if needed and add/update node
            if not detached:
                raise ConsistencyError(f"Node ({node.key()}) already exists and is not detached.")

            # Get the old creator before this information is lost.
            old_creator, old_creator_detached = node.creator_and_detached()
            # Replace the old creator by the new one.
            if creator is None:
                creator_i = None
                detached = True
            else:
                creator_i = creator.i
                detached = creator.is_detached()
            self.db.execute(
                "UPDATE node SET creator = ?, detached = ? WHERE i = ?",
                (creator_i, detached, node.i),
            )
            # The old creator, if any, no longer records everything it created.
            # It is left in the graph (it may still be recycled), but not as a skippable node.
            # It is removed by the next `Trellis.delete_detached` unless it is recycled before that.
            if old_creator is not None:
                if not old_creator_detached:
                    raise ConsistencyError("Old creator of detached node is not detached.")
                old_creator.after_lost_product()
            # Cut all ties to sources, so this node starts from a clean slate.
            node.del_all_sources()
            # Since this node is recreated, it cannot have created other nodes (yet).
            for product in node.products():
                product.detach()
        elif node_type is Root:
            # The node table's CHECK constraints forbid ever storing the root node with a
            # NULL creator or as detached, even momentarily, so it must be its own creator
            # from the single INSERT that creates it. A second attempt to create a root node
            # (from anywhere, at any time) always retries this same i=1 INSERT, so the
            # PRIMARY KEY constraint on node.i alone guarantees only one root ever exists --
            # no Python-side "only one root" guard is needed.
            self.db.execute(
                "INSERT INTO node (i, kind, label, creator, detached) VALUES (1, ?, ?, 1, FALSE)",
                (node_type.kind(), label),
            )
            node_i = 1
            node = node_type(self, node_i, label)
        else:
            detached = True if creator is None else creator.is_detached()
            # Add new node
            cur = self.db.execute(
                "INSERT INTO node (kind, label, creator, detached) VALUES (?, ?, ?, ?)",
                (node_type.kind(), label, None if creator is None else creator.i, detached),
            )
            node_i = cur.lastrowid
            node = node_type(self, node_i, label)
        node.initialize_row(**kwargs)
        node.validate_row()
        return node

    def try_recycle(
        self, node_type: type[NodeType], creator: Node, label: str = "", **kwargs
    ) -> NodeType | None:
        """Fully recycle a compatible detached node, if there is one.

        Unlike the fallback in `create`, which reuses only the node row,
        a fully recycled node keeps its sources, sinks, products and satellite data.
        Whether the given arguments are compatible with the detached node
        is decided by the node's `can_recycle` method.

        Parameters
        ----------
        node_type
            Subclass of Node.
        creator
            The node that recreates the node to be recycled.
        label
            The label of the node.
        kwargs
            Additional node-specific arguments,
            passed to `can_recycle` and `after_recycle`.

        Returns
        -------
        node
            The fully recycled node,
            or `None` when there is no compatible detached node with this label.
        """
        label = node_type.adjust_label(label, **kwargs)
        node, detached = self.find_and_detached(node_type, label)
        if node is None or not detached or not node.can_recycle(**kwargs):
            return None
        # Node.reattach validates the new creator and cleans up the old one.
        node.reattach(creator)
        node.after_recycle(**kwargs)
        node.validate_row()
        logger.info("Recycle node: %s", node.key())
        return node

    def delete_detached(self):
        """Delete all detached nodes that can be removed safely.

        This is the only place where nodes are deleted from the graph.
        Everything else that lets go of a node merely detaches it,
        so this method decides when it is actually gone.
        """
        # Creators of the deleted nodes, checked after the loop has settled.
        creator_is = set()
        cleaned_some = True
        while cleaned_some:
            cleaned_some = False
            # Look for detached nodes that are leaves in both graphs:
            # no products (no outgoing creator edge) and no sinks (no outgoing dependency edge).
            # Such a node is referenced by no other row, so it can be deleted outright.
            # (`node.creator` and `dependency.source` are foreign keys without ON DELETE action,
            # so deleting a node that still has products or sinks fails on a constraint.)
            #
            # The two conditions hold back deletion for different reasons:
            #
            # * Products of a detached node are themselves detached, always:
            #   `Node.detach` propagates the flag recursively over creator -> product edges, and
            #   `_check_consistency` verifies this both per row and globally (reachability).
            #   Every product is therefore a candidate for this same loop,
            #   and the products condition merely forces the cascade to run bottom-up.
            # * Sinks may well be attached, because detachment propagates along creator edges
            #   only, never along dependency edges. A detached file that is still an input of an
            #   attached step must be kept: deleting it would silently drop that step's input.
            #   Such nodes are reported to the user instead, see `pending.py`'s dead-end-file
            #   detection (`_INSERT_PEND_DEAD_FILE`), which flags a detached blocking input.
            #
            # The intended fixed point is therefore: every surviving detached node is held
            # (directly or indirectly) by an attached sink.
            # Note that a cycle of creator and dependency edges within the detached part of the
            # graph is a fixed point of this loop as well, without any attached sink involved,
            # e.g. a step that declares one of its own products as a (dynamic) input.
            # Whatever the reason for its survival, a detached node that lost a product this way
            # is no longer a complete record of what it created, see after the loop.
            query = (
                "SELECT i, kind, label, creator FROM node WHERE detached AND "
                "NOT EXISTS (SELECT 1 FROM node AS cnode WHERE node.i = cnode.creator) AND "
                "NOT EXISTS (SELECT 1 FROM dependency WHERE node.i = dependency.source)"
            )
            for i, kind, label, creator_i in self.db.execute(query):
                node = self.node_from_row(i, kind, label)
                cleaned_some = True
                node.del_all_sources()
                node.before_delete()
                self.db.execute("DELETE FROM node where i = ?", (i,))
                creator_is.discard(i)
                if creator_i is not None:
                    creator_is.add(creator_i)

        # A creator that lost a product above and is still present no longer records everything
        # it created, so it cannot be trusted to reproduce its products when it is recycled later.
        # It survived either because a cycle keeps it alive,
        # or because one of its other products is (indirectly) held by an attached sink.
        # The node itself is worth keeping: it is still recyclable, just not skippable.
        # This must happen after the loop has settled, so it does not fire for creators
        # that a later iteration deletes anyway.
        for creator_i in creator_is:
            row = self.db.execute(
                "SELECT kind, label FROM node WHERE i = ?", (creator_i,)
            ).fetchone()
            if row is not None:
                kind, label = row
                self.node_from_row(creator_i, kind, label).after_lost_product()
