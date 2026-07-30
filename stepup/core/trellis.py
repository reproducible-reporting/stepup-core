# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""StepUp's abstract implementation of the provenance and dependency graphs."""

import logging
import sqlite3
from collections.abc import Iterable, Iterator
from typing import Self, TypeVar

import attrs

from .exceptions import CyclicError, GraphError
from .sqlite3 import DBSession

__all__ = ("Node", "NodeType", "Root", "Trellis")


logger = logging.getLogger(__name__)


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

RECURSE_SINKS_SINGLE = """
WITH RECURSIVE all_sink(current) AS (
    -- Initial: Set initial node
    SELECT ? AS current
    UNION
    -- Recursion: Follow edges by selecting sinks of current
    SELECT sink AS current
    FROM dependency INNER JOIN all_sink ON source = current
)
"""

SELECT_WALK = """
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

    - `con`: the SQLite connection.
    - `i`: the identifier of the node in the database.
    - `kind`: determines the subclass of `Node` to use.
    - `label`: a unique (within its kind) label for the node.

    All other information related to this node is directly taken from or stored in the database.

    Subclasses may override the following:

    - `kind` to control the formatting of the key string.
    - `schema` to extend the trellis schema.
    - `create_label` to override the user-provided label of a node.
    - `initialize` to create or update rows for new nodes outside the default Trellis tables.
    - `validate` to check if the necessary rows outside the default Trellis tables are made.
    - `format_properties` to define the properties of the node.
    - `lost_product` is called when a detached node loses a product node.
    - `clean` to decide if a detached node can be removed and to release resources.
    - `can_recycle` to decide if a detached node can be fully recycled by `Trellis.recycle`.
    - `update_recycled` to update the mutable declared properties after a full recycle.
    """

    graph: "Trellis" = attrs.field(repr=False)
    """The Trellis object that contains the node."""

    i: int = attrs.field()
    """The identifier of the node in the database."""

    label: str = attrs.field()
    """The label of the node.

    While this can be derived from the database, it is stored here for convenience,
    since it is considered immutable.
    """

    @property
    def db(self) -> DBSession:
        """The SQLite database."""
        return self.graph.db

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

    def clean(self):
        """Perform a cleanup right before the detached node is removed from the graph."""

    def lost_product(self):
        """Invalidate cached results because a product of this detached node was removed.

        This keeps the node (and its recyclable metadata) in the graph:
        only `Trellis.clean` ever deletes nodes.
        It is called on a detached node that loses a product,
        either because the product was deleted (`Trellis.clean`)
        or because another creator took it over (`Trellis.create` and `Node.recycle`).
        Such a node is no longer a faithful record of what it created,
        so subclasses must drop whatever would let them be skipped when recycled later.

        The default implementation does nothing,
        which is correct for nodes that store no such result.
        """

    def can_recycle(self, **kwargs) -> bool:
        """Decide whether this detached node may be fully recycled by `Trellis.recycle`.

        A full recycle keeps the node's edges, products and satellite data,
        so it is only allowed when the given arguments are compatible
        with the information already stored for this node.

        The default implementation is to never recycle fully, which subclasses can override.

        Callers that want full recycling should try `Trellis.recycle` first
        and fall back to `Trellis.create`, which reuses only the node row,
        if the `recycle` method returns `None`.
        """
        return False

    def update_recycled(self, **kwargs):
        """Update the mutable declared properties of this node after a full recycle."""

    #
    # Getters and Iterators
    #

    def is_alive(self) -> bool:
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
        return self.graph.node_classes[kind](self.graph, i, label)

    def creator_detached(self) -> tuple[Self, bool] | tuple[None, None]:
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
        return self.graph.node_classes[kind](self.graph, i, label), detached

    def products(self, node_type: type[NodeType] = Self) -> Iterator[NodeType]:
        """Iterate over (a subset of) products of this node."""
        query = "SELECT i, kind, label FROM node WHERE creator = ?"
        data = [self.i]
        if node_type is not Self:
            query += " AND kind = ?"
            data.append(node_type.kind())
        for i, kind, label in self.db.execute(query, data):
            yield self.graph.node_classes[kind](self.graph, i, label)

    def products_str(self, node_type: type[NodeType] = Self) -> Iterator[str]:
        """Iterate over (a subset of) products of this node, formatted as strings."""
        sql = "SELECT kind, label, detached FROM node WHERE creator = ?"
        data = [self.i]
        if node_type is not Self:
            sql += " AND kind = ?"
            data.append(node_type.kind())
        sql += " ORDER BY kind, label"
        for kind, label, detached in self.db.execute(sql, data):
            node_str = f"{kind}:{label}"
            if detached:
                node_str = f"({node_str})"
            yield node_str

    def _dependencies(
        self,
        node_type: type[NodeType] = Self,
        include_detached: bool = False,
        do_sources: bool = True,
    ) -> Iterator[NodeType]:
        sql = "SELECT node.i, kind, label FROM node JOIN dependency ON node.i = "
        if do_sources:
            sql += "source WHERE sink = ?"
        else:
            sql += "sink WHERE source = ?"
        data = [self.i]
        if node_type is not Self:
            sql += " AND kind = ?"
            data.append(node_type.kind())
        if not include_detached:
            sql += " AND NOT detached"
        for i, kind, label in self.db.execute(sql, data):
            yield self.graph.node_classes[kind](self.graph, i, label)

    def sources(
        self, node_type: type[NodeType] = Self, include_detached: bool = False
    ) -> Iterator[NodeType]:
        """Iterate over nodes that supply to this one."""
        yield from self._dependencies(node_type, include_detached, do_sources=True)

    def sinks(
        self, node_type: type[NodeType] = Self, include_detached: bool = False
    ) -> Iterator[NodeType]:
        """Iterate over nodes that consume from this one."""
        yield from self._dependencies(node_type, include_detached, do_sources=False)

    def _dependencies_str(
        self,
        node_type: type[NodeType] = Self,
        do_sources: bool = True,
    ) -> Iterator[str]:
        sql = "SELECT kind, label, detached FROM node JOIN dependency ON node.i = "
        sql += " source WHERE sink = ?" if do_sources else " sink WHERE source = ?"
        data = [self.i]
        if node_type is not Self:
            sql += " AND kind = ?"
            data.append(node_type.kind())
        sql += " ORDER BY kind, label"
        for kind, label, detached in self.db.execute(sql, data):
            node_str = f"{kind}:{label}"
            if detached:
                node_str = f"({node_str})"
            yield node_str

    def sources_str(self, node_type: type[NodeType] = Self) -> Iterator[str]:
        """Iterate over nodes that supply to this one, formatted as strings."""
        yield from self._dependencies_str(node_type, do_sources=True)

    def sinks_str(self, node_type: type[NodeType] = Self) -> Iterator[str]:
        """Iterate over nodes that consume from this one, formatted as strings."""
        yield from self._dependencies_str(node_type, do_sources=False)

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

    def recycle(self, new_creator: "Node"):
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
        old_creator, old_creator_detached = self.creator_detached()
        self.db.execute(
            "UPDATE node SET creator = ?, detached = FALSE WHERE i = ?", (new_creator.i, self.i)
        )
        # The old creator, if any, no longer records everything it created.
        # It is left in the graph (it may still be recycled), but not as a skippable node.
        if old_creator is not None:
            if not old_creator_detached:
                raise GraphError("Old creator of detached node is not detached.")
            old_creator.lost_product()
        # Propagate the detached=FALSE property to all product nodes.
        self.db.execute(RECURSIVELY_SET_DETACHED, (self.i, False))

    def check_no_cycle_batch(self, source_is: Iterable[int]) -> None:
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
        cur = self.db.execute(RECURSE_SINKS_SINGLE + SELECT_WALK, (self.i,))
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
            e.g. via `check_no_cycle_batch`, that this edge cannot introduce a cycle.

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
            cur = self.db.execute(RECURSE_SINKS_SINGLE + SELECT_CYCLIC, (self.i, source.i))
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

    def del_sources(self, sources: list["Node"] | None = None):
        """Delete given sources.

        Without arguments, all sources of the current node are deleted.
        """
        if sources is None:
            self.db.execute("DELETE FROM dependency WHERE sink = ?", (self.i,))
        else:
            self.db.executemany(
                "DELETE FROM dependency WHERE source = ? AND sink = ?",
                ((source.i, self.i) for source in sources),
            )


@attrs.define(frozen=True)
class Root(Node):
    """The root node of the provenance and dependency graph.

    (Indirect) products of the root node are considered active nodes in the graph.
    Nodes that are not connected (indirectly) to the root node are considered detached,
    and will be removed when the Trellis.clean method is called.
    """

    def clean(self):
        """Always raise, since the root node is never detached and thus never cleaned up."""
        raise AssertionError("Root node cannot be cleaned")

    def lost_product(self):
        """Always raise, since the root node can never lose a product this way.

        `lost_product` is only called on a detached node:
        `Trellis.clean` only deletes detached nodes,
        and `Trellis.create` and `Node.recycle` only take a product away
        from an already-detached creator.
        The root node is never detached.
        """
        raise AssertionError("Root node cannot be the creator of a detached node.")


@attrs.define(eq=False)
class Trellis:
    """Base class for provenance and dependency graphs.

    Subclasses should implement at least the following:

    - Override `default_node_classes` to define the types of nodes that are supported.
    """

    # The database lock managing the SQLite connection where the graph is stored.
    db: DBSession = attrs.field()

    # The types of nodes that are supported.
    node_classes: dict[str, type[Node]] = attrs.field(init=False)

    @node_classes.default
    def _default_node_classes(self) -> dict[str, type[Node]]:
        return {node_class.kind(): node_class for node_class in self.default_node_classes()}

    @staticmethod
    def default_node_classes() -> list[type[Node]]:
        return [Root]

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
        # Schema 4 became outdated due to the v4.0.0 rewrite
        # (schema 4 itself was last released in v3.2.3):
        # - Directory file nodes are no longer part of the graph (implicit instead);
        #   deferred globs were removed in favor of static trees;
        #   "orphan" was renamed to "detached".
        # - File hashing switched from Blake2B to SHA-256, and the file table's separate
        #   digest/mode/mtime/size/inode columns were merged into a single nullable JSON
        #   `hash` column (NULL replacing the old sentinel values). Added
        #   `FileState.UNCONFIRMED` to distinguish truly missing files from those still
        #   needing a hash check.
        # - The step table was reworked for the new scheduling algorithm: many fields
        #   added/removed, DEFAULT clauses added, step hashes collapsed to a single JSON
        #   blob, and labels now store the raw command line instead of an
        #   action-name-prefixed one. The QUEUED state was removed and CHECKING was added
        #   (hash-checking a step for a possible skip without consuming a resource slot).
        # - Step readiness/safety/postponement bookkeeping (previously read-branch-write in
        #   Python) moved into triggers and CHECK constraints: the postponed/state CHECK,
        #   `step_clear_postponed`, `step_flag_check_safe`/`step_flag_check_after_duration`,
        #   and the `_has_hash`/`_ready`/`_check_ready` columns with their maintaining
        #   triggers and the `step_dispatch`/`step_check_ready` indexes. New
        #   `step_need_count`/`path_list`/`node_list` temp tables avoid full-table scans for
        #   counts and batch lookups.
        # - Added re-entrant `hold()`/`release()` support: `step._holding`,
        #   `step._safe_ignoring_hold`, and the `step_reset_holding` trigger.
        # - Graph invariants (creator/dependency kind rules, single root node) moved from
        #   Python (`Workflow._check_creator`/`_check_source`, `Trellis._check_creator`,
        #   and their `Node`/`Trellis` call sites, all removed) into node/dependency CHECK
        #   constraints and triggers.
        # - Added `step_outcome` (stdout/stderr) and `step_subprocess` (subprocess
        #   invocations, keyed by rowid instead of an explicit seq column) tables.
        # - `nglob_multi.data` changed from a pickle blob to JSON.
        # - `ON DELETE CASCADE` added to all satellite tables; indexes tuned; `auto_vacuum`
        #   set to INCREMENTAL with a paired vacuum worker.

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
        # self-creation are enforced by CHECK constraints on the node table, so they no longer
        # need a Python-side check here.
        sql = (
            "SELECT node.i, node.kind, node.label, node.creator, node.detached, cnode.detached "
            "FROM node LEFT JOIN node AS cnode ON node.creator = cnode.i"
        )
        for row in self.db.execute(sql):
            i, kind, label, creator_i, detached, creator_detached = row
            creator_detached = creator_i is None or creator_detached
            if detached:
                if not creator_detached:
                    node = self.node_classes[kind](self, i, label)
                    raise GraphError(f"Detached node has attached creator: {node.key()}")
            elif creator_detached:
                node = self.node_classes[kind](self, i, label)
                raise GraphError(f"Attached node has detached creator: {node.key()}")
        # The per-row checks above only catch immediate (single-hop) inconsistencies between a
        # node and its own creator. A longer cycle in the creator chain (e.g. A creates B creates
        # A) can pass every one of those checks while never actually connecting to the root, so
        # also verify global reachability from the root along creator -> product edges.
        cur = self.db.execute(CHECK_DETACHED_REACHABILITY, (self._root.i,))
        row = cur.fetchone()
        if row is not None:
            i, kind, label, detached = row
            node = self.node_classes[kind](self, i, label)
            if detached:
                raise GraphError(
                    f"Detached node is reachable from root via creator chain: {node.key()}"
                )
            raise GraphError(
                f"Attached node is not reachable from root via creator chain: {node.key()}"
            )
        for node in self.nodes():
            node.validate()

    #
    # Basic attributes and introspection
    #

    @property
    def root(self) -> Root:
        return self._root

    def find(self, node_type: type[NodeType], label: str) -> NodeType | None:
        """Return the node for the given node class and label."""
        sql = "SELECT i FROM node WHERE kind = ? AND label = ?"
        data = (node_type.kind(), label)
        row = self.db.execute(sql, data).fetchone()
        return None if row is None else node_type(self, row[0], label)

    def find_detached(
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

    def node(self, node_type: type[NodeType], i: int) -> NodeType | None:
        """Return the node for the given node class and identifier."""
        sql = "SELECT kind, label FROM node WHERE i = ?"
        data = (i,)
        row = self.db.execute(sql, data).fetchone()
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
        """Iterate over all nodes, optionally filtered by kind."""
        query = "SELECT i, kind, label FROM node"
        data = []
        words = ["WHERE", "AND"]
        if node_type is not Node:
            query += f" {words.pop(0)} kind = ?"
            data.append(node_type.kind())
        if not include_detached:
            query += f" {words.pop(0)} NOT detached"
        for i, kind, label in self.db.execute(query, data):
            yield self.node_classes[kind](self, i, label)

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
            node = self.node_classes[kind](self, i, label)
            creator = None if ci is None else self.node_classes[ckind](self, ci, clabel)
            lines.append(node.key(detached))
            for name, value in node.format_properties():
                lines.append(f"{name:>20s} = {value!s}")
            pairs = []
            if ci is not None and (label != clabel):
                pairs.append(("creator", creator.key(cdetached)))
            pairs.extend(("source", other_str) for other_str in node.sources_str())
            pairs.extend(
                ("product", other_str) for other_str in node.products_str() if other_str != "root:"
            )
            pairs.extend(("sink", other_str) for other_str in node.sinks_str())
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
        """
        # Sanity checking
        if not isinstance(node_type, type):
            raise TypeError(f"Argument node_type must be a type, got {node_type}")
        if not issubclass(node_type, Node):
            raise TypeError(f"Argument node_type must be a subclass of Node, got {node_type}")
        if not (isinstance(creator, Node) or creator is None):
            raise TypeError(f"Argument creator must be a Node or None, got {type(creator)}")
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
            self.db.execute(
                "UPDATE node SET creator = ?, detached = ? WHERE i = ?",
                (creator_i, detached, node.i),
            )
            # The old creator, if any, no longer records everything it created.
            # It is left in the graph (it may still be recycled), but not as a skippable node.
            # It is removed by the next `Trellis.clean` unless it is recycled before that.
            if old_creator is not None:
                if not old_creator_detached:
                    raise GraphError("Old creator of detached node is not detached.")
                old_creator.lost_product()
            # Cut all ties to sources, so this node starts from a clean slate.
            node.del_sources()
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
        node.initialize(**kwargs)
        node.validate()
        return node

    def recycle(
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
            passed to `can_recycle` and `update_recycled`.

        Returns
        -------
        node
            The fully recycled node,
            or `None` when there is no compatible detached node with this label.
        """
        label = node_type.create_label(label, **kwargs)
        node, detached = self.find_detached(node_type, label)
        if node is None or not detached or not node.can_recycle(**kwargs):
            return None
        # Node.recycle validates the new creator and cleans up the old one.
        node.recycle(creator)
        node.update_recycled(**kwargs)
        node.validate()
        logger.info("Recycle node: %s", node.key())
        return node

    def clean(self):
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
            #   Such nodes are reported to the user instead, see `Workflow.detached_inp_paths`.
            #
            # The intended fixed point is therefore: every surviving detached node is held
            # (directly or indirectly) by an attached sink.
            # Note that a cycle of creator and dependency edges within the detached part of the
            # graph is a fixed point of this loop as well, without any attached sink involved,
            # e.g. a step that declares one of its own products as an (amended) input.
            # Whatever the reason for its survival, a detached node that lost a product this way
            # is no longer a complete record of what it created, see after the loop.
            query = (
                "SELECT i, kind, label, creator FROM node WHERE detached AND "
                "NOT EXISTS (SELECT 1 FROM node AS cnode WHERE node.i = cnode.creator) AND "
                "NOT EXISTS (SELECT 1 FROM dependency WHERE node.i = dependency.source)"
            )
            for i, kind, label, creator_i in self.db.execute(query):
                node = self.node_classes[kind](self, i, label)
                cleaned_some = True
                node.del_sources()
                node.clean()
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
                self.node_classes[kind](self, creator_i, label).lost_product()
