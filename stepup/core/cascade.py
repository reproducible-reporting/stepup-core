# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright (C) 2024 Toon Verstraelen
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
"""A Cascade instance is StepUp's abstract implementation of the workflow graph.

It introduces two types of edges (creator-product) and (supplier-consumer),
and several essential `Node` classes: `Root` and `Vacuum`.
All other nodes and their logic are implemented in `stepup.core.workflow`.

The separation between `Cascade` and `Workflow` allows for testing a well-defined subset,
before building more complexity on top of it.
"""

import lzma
from collections.abc import Iterator
from typing import Any, Self

import attrs
import msgpack

from .assoc import Assoc, AssocView, many_to_many, one_to_many
from .exceptions import CyclicError, GraphError
from .utils import classproperty, lookupdict

__all__ = ("Node", "Root", "Vacuum", "Cascade", "get_kind")


@attrs.define
class Node:
    """Parent class of node-specific information and actions."""

    _key: str = attrs.field(init=False, default="todo:")

    @classproperty
    def kind(cls):
        return cls.__name__.lower()

    @classmethod
    def key_tail(cls, data: dict[str, Any], lookup: list[str] | None = None) -> str:
        """Subclasses must implement the key tail and accept both JSON or attrs dicts."""
        raise NotImplementedError

    def __attrs_post_init__(self):
        tail = self.key_tail(attrs.asdict(self, recurse=False))
        self._key = f"{self.kind}:{tail}"

    @property
    def key(self) -> str:
        """A unique string identifier, possible derived from a few defining attributes."""
        return self._key

    @classmethod
    def structure(cls, cascade: "Cascade", keys: list[str], data: dict) -> Self:
        raise NotImplementedError

    def unstructure(self, cascade: "Cascade", lookup: dict[str, int]) -> dict:
        raise NotImplementedError

    def format_properties(self, cascade: "Cascade") -> Iterator[tuple[str, str]]:
        raise NotImplementedError

    def recycle(self, cascade: "Cascade", old: Self | None):
        """Copy useful information from older orphaned version of the node, if any."""
        pass

    def orphan(self, cascade: "Cascade"):
        """This method is called when the creator of a node is set to Creator.vacuum."""
        pass

    def cleanup(self, cascade: "Cascade"):
        """Perform a cleanup right before the orphaned node is removed from the graph."""
        pass


@attrs.define
class Root(Node):
    """The top-level creator in the cascade."""

    _version: str = attrs.field()

    @classmethod
    def key_tail(cls, data: dict[str, Any], lookup: list[str] | None = None) -> str:
        return ""

    @property
    def version(self):
        return self._version

    @classmethod
    def structure(cls, workflow: "Cascade", keys: list[str], data: dict) -> Self:
        return cls(version=data["v"])

    def unstructure(self, workflow: "Cascade", lookup: dict[str, int]) -> dict:
        return {"v": self._version}

    def format_properties(self, workflow: "Cascade") -> Iterator[tuple[str, str]]:
        yield "version", self._version

    def orphan(self, cascade: "Cascade"):
        raise NotImplementedError

    def cleanup(self, cascade: "Cascade"):
        raise NotImplementedError


@attrs.define
class Vacuum(Node):
    """The creator assigned to nodes that can be cleaned up later."""

    @classmethod
    def key_tail(cls, data: dict[str, Any], lookup: list[str] | None = None) -> str:
        return ""

    @classmethod
    def structure(cls, workflow: "Cascade", keys: list[str], data: dict) -> Self:
        return cls()

    def unstructure(self, workflow: "Cascade", lookup: dict[str, int]) -> dict:
        return {}

    def format_properties(self, workflow: "Cascade") -> Iterator[tuple[str, str]]:
        yield from []

    def orphan(self, cascade: "Cascade"):
        raise NotImplementedError

    def cleanup(self, cascade: "Cascade"):
        raise NotImplementedError


def get_kind(key: str) -> str:
    return key[: key.find(":")]


def _check_key(key: str, node_classes: dict[str, type]):
    kind = get_kind(key)
    if kind not in node_classes:
        raise KeyError(f"No Body subclass for {key}")


@attrs.define
class Cascade:
    # Dictionary of all nodes in their key
    nodes: dict[str, Node] = attrs.field(factory=dict)
    # Association (kind, keys_of_kind)
    kinds: Assoc[str, str] = attrs.field(factory=one_to_many)
    # Association (creator, product) and its inverse
    products: Assoc[str, str] = attrs.field(factory=one_to_many)
    creators: AssocView[str, str] = attrs.field(init=False)
    # Association (supplier, consumer) and its inverse
    consumers: Assoc[str, str] = attrs.field(factory=many_to_many)
    suppliers: AssocView[str, str] = attrs.field(init=False)
    # The types of nodes that are allowed
    node_classes: dict[str, type[Node]] = attrs.field(init=False)

    @creators.default
    def _default_creators(self) -> AssocView[str, str]:
        return self.products.inverse

    @suppliers.default
    def _default_suppliers(self) -> AssocView[str, str]:
        return self.consumers.inverse

    #
    # Initialization
    #

    def check_consistency(self):
        # Check whether the initial graph satisfies all constraints.
        if self.get_creator("root:") != "root:":
            raise ValueError("Invalid cascade: root node does not create itself")
        if self.get_creator("vacuum:") != "root:":
            raise ValueError("Invalid cascade: vacuum node not created by root")
        for node_key in self.nodes:
            if node_key not in self.creators:
                raise ValueError(f"Node has no creator: {node_key}")
            if get_kind(node_key) not in self.node_classes:
                raise ValueError(f"Node has unknown node class: {node_key}")

    @node_classes.default
    def _default_node_classes(self) -> dict[str, type[Node]]:
        return self.default_node_classes()

    @staticmethod
    def default_node_classes() -> dict[str, type[Node]]:
        return {"root": Root, "vacuum": Vacuum}

    @classmethod
    def from_scratch(cls):
        cascade = cls()
        cascade.create(Root("v1"), "root:")
        cascade.create(Vacuum(), "root:")
        cascade.check_consistency()
        return cascade

    #
    # Serialization
    #

    @classmethod
    def structure(cls, state) -> Self:
        cascade = cls()
        strings = [None] * len(state["nodes"]) + state.get("strings", [])
        for inode, node_data in enumerate(state["nodes"]):
            kind = node_data["c"]
            tail = cascade.node_classes[kind].key_tail(node_data, strings)
            strings[inode] = f"{kind}:{tail}"
        for node_data in state["nodes"]:
            if not isinstance(node_data, dict):
                raise TypeError(f"Do not know how to process node: {node_data}")
            kind = node_data["c"]
            node_class = cascade.node_classes[kind]
            node = node_class.structure(cascade, strings, node_data.copy())
            if node.key in cascade.nodes:
                raise ValueError(f"Duplicate key: {node.key}")
            cascade.nodes[node.key] = node
            cascade.kinds.add(kind, node.key)
        for idx_from, idx_to in state["products"]:
            cascade.products.add(strings[idx_from], strings[idx_to])
        for idx_from, idx_to in state["consumers"]:
            cascade.consumers.add(strings[idx_from], strings[idx_to])
        return cascade

    def unstructure(self):
        lookup = lookupdict()
        state = {"nodes": [], "products": [], "consumers": []}
        for key in self.nodes:
            lookup[key] = len(lookup)
        for node in self.nodes.values():
            node_data = {"c": node.kind}
            node_data.update(node.unstructure(self, lookup))
            state["nodes"].append(node_data)
        for key_src, key_dst in self.products.pairs():
            state["products"].append([lookup[key_src], lookup[key_dst]])
        state["products"].sort()
        for key_src, key_dst in self.consumers.pairs():
            state["consumers"].append([lookup[key_src], lookup[key_dst]])
        state["consumers"].sort()

        # Convert lookup to list, remove node keys and store
        strings = lookup.get_list()[len(self.nodes) :]
        if len(strings) > 0:
            state["strings"] = strings
        return state

    @classmethod
    def from_file(cls, path: str) -> Self:
        with lzma.open(path, "rb") as fh:
            cascade = cls.structure(msgpack.load(fh))
        if cascade.get_root().version != "v1":
            raise ValueError(f"The workflow loaded from {path} is not of version v1.")
        return cascade

    def to_file(self, path: str):
        with lzma.open(path, "wb") as fh:
            msgpack.dump(self.unstructure(), fh)

    def format_str(self):
        lines = []
        for key, node in self.nodes.items():
            if key == "vacuum:":
                continue
            creator_key = self.get_creator(key)
            assert creator_key is not None
            if creator_key == "vacuum:":
                lines.append(f"({key})")
            else:
                lines.append(key)
            for name, value in node.format_properties(self):
                lines.append(f"{name:>20s} = {value!s}")
            pairs = []
            if not (creator_key == "vacuum:" or key == "root:"):
                pairs.append(("created by", creator_key))
            for other_key in self.get_suppliers(key, include_orphans=True):
                pairs.append(("consumes", other_key))
            for other_key in self.get_products(key, include_orphans=True):
                if other_key != "root:":
                    pairs.append(("creates", other_key))
            for other_key in self.get_consumers(key, include_orphans=True):
                pairs.append(("supplies", other_key))
            for role, other_key in pairs:
                if other_key == "vacuum:":
                    continue
                if self.get_creator(other_key) == "vacuum:":
                    lines.append(f"{role:>20s}   ({other_key})")
                else:
                    lines.append(f"{role:>20s}   {other_key}")
            lines.append("")
        return "\n".join(lines)

    #
    # Type-annotated and type-checked node access
    #

    def get_root(self) -> Root:
        node = self.nodes.get("root:")
        if not isinstance(node, Root):
            raise TypeError(f"The root node the wrong type {type(node)}.")
        return node

    #
    # Introspection
    #

    def is_orphan(self, key: str) -> bool:
        return self.creators[key] == "vacuum:"

    #
    # Sorting getters
    #

    def get_creator(self, key: str) -> str | None:
        """Return the other key that created this key."""
        return self.creators[key]

    def get_products(
        self, key: str, kind: str | None = None, include_orphans: bool = False
    ) -> list[str]:
        """Return other keys created by key."""
        assert kind is None or kind in self.node_classes
        return sorted(
            other
            for other in self.products.get(key, ())
            if (include_orphans or not self.is_orphan(other))
            and (kind is None or kind == get_kind(other))
        )

    def get_suppliers(
        self, key: str, kind: str | None = None, include_orphans: bool = False
    ) -> list[str]:
        """Other keys that require key, only those that are still fresh."""
        assert kind is None or kind in self.node_classes
        return sorted(
            other
            for other in self.suppliers.get(key, ())
            if (include_orphans or not self.is_orphan(other))
            if (kind is None or kind == get_kind(other))
        )

    def get_consumers(
        self, key: str, kind: str | None = None, include_orphans: bool = False
    ) -> list[str]:
        """Other keys required by key, only those that are still fresh."""
        assert kind is None or kind in self.node_classes
        return sorted(
            other
            for other in self.consumers.get(key, ())
            if (include_orphans or not self.is_orphan(other))
            and (kind is None or kind == get_kind(other))
        )

    def get_nodes(self, kind: str | None = None, include_orphans: bool = False):
        assert kind is None or kind in self.node_classes
        keys = self.nodes if kind is None else self.kinds.get(kind, ())
        keys = sorted(key for key in keys if (include_orphans or not self.is_orphan(key)))
        return [self.nodes[key] for key in keys]

    #
    # Graph modifications
    #

    def _iter_cycles(self, src_key: str, dst_key: str) -> Iterator[tuple[str]]:
        """Iterate over cycles that point from `dst_key` back to `src_key`.

        Notes
        -----
        This method only consider supplier-consumer edges.
        """
        if src_key != "root:":
            if src_key == dst_key:
                yield (src_key,)
            for supplier_key in self.suppliers.get(src_key, ()):
                for cycle in self._iter_cycles(supplier_key, dst_key):
                    yield (src_key, *cycle)

    def report_cyclic(self, src_key: str, dst_key: str):
        """Raise an informative exception when the new edge would make the directed graph cyclic.

        Notes
        -----
        - This method is rather slow and only called when there is a known problem.
          It is helpful because it provides more useful (and expensive) feedback.
        - This method only consider supplier-consumer edges.
        """
        lines = []
        for cycle in self._iter_cycles(src_key, dst_key):
            lines.append("cycle:")
            for key in cycle:
                lines.append(f"  {key}")
            lines.append("")
        if len(lines) > 0:
            lines[:0] = [
                "New relation introduces cyclic dependency",
                "",
                f"src = {src_key}",
                f"dst = {dst_key}",
                "",
            ]
            raise CyclicError("\n".join(lines))

    def walk_suppliers(self, key: str, visited: set[str]):
        """Efficiently collect all suppliers of `key` and add them to `visited`."""
        if key in ["root:", "vacuum:"]:
            return
        visited.add(key)
        for supplier_key in self.suppliers.get(key, ()):
            if supplier_key not in visited:
                self.walk_suppliers(supplier_key, visited)

    def walk_consumers(self, key: str, visited: set[str]):
        """Efficiently collect all consumers of `key` and add them to `visited`."""
        if key in ["root:", "vacuum:"]:
            return
        visited.add(key)
        for consumer_key in self.consumers.get(key, ()):
            if consumer_key not in visited:
                self.walk_consumers(consumer_key, visited)

    def check_cyclic(self, src_key: str, dst_key: str):
        """Raise an informative exception when the new edge would introduce a cyclic dependency."""
        visited = set()
        self.walk_suppliers(src_key, visited)
        if dst_key in visited:
            self.report_cyclic(src_key, dst_key)

    def create(self, node: Node, creator_key: str) -> str:
        """Add a newly created node with reference to its creator."""
        # Sanity checking
        if not isinstance(creator_key, str):
            raise TypeError(f"Argument creator_key must be a string, got {type(creator_key)}")
        kind = node.kind
        if kind not in self.node_classes:
            raise TypeError(f"Unknown node class: {kind}")
        node_class = self.node_classes[kind]
        if node_class is not node.__class__:
            raise TypeError(f"Node class mismatch: given {node.__class__} is not {node_class}")
        # Recycle old data if needed and add/update node
        old = self.nodes.get(node.key)
        if old is not None:
            if not self.is_orphan(node.key):
                raise ValueError("Node already exists and is not orphan.")
            # Sanity check: An orphan node cannot be a creator of other nodes.
            assert node.key not in self.products
            # Remove "vacuum:" creator.
            self.creators.discard(node.key)
            # Old suppliers are discarded because they are defined after creating a node.
            self.suppliers.discard(node.key)
            # Old consumers stay in place.
        self.nodes[node.key] = node
        self.kinds.add(kind, node.key)
        node.recycle(self, old)
        # Add creator relation, if creator is known
        _check_key(creator_key, self.node_classes)
        if creator_key not in self.nodes:
            raise ValueError(f"Non-existing creator: {creator_key}")
        try:
            self.check_cyclic(creator_key, node.key)
        except Exception:
            # Instead of creating the cycle, the node is created as an orphan,
            # to make sure the graph remains meaningful. All nodes must have a creator.
            self.products.add("vacuum:", node.key)
            raise
        self.products.add(creator_key, node.key)
        return node.key

    def supply(self, supplier_key, consumer_key):
        """Create a supplier-consumer relation."""
        _check_key(supplier_key, self.node_classes)
        _check_key(consumer_key, self.node_classes)
        if supplier_key not in self.nodes:
            raise GraphError(f"Unknown key (supplier): {supplier_key}")
        if consumer_key not in self.nodes:
            raise GraphError(f"Unknown key (consumer): {consumer_key}")
        if consumer_key in self.consumers.get(supplier_key, []):
            raise GraphError(f"Duplicate relation: '{supplier_key}' '{consumer_key}'")
        self.check_cyclic(supplier_key, consumer_key)
        self.consumers.add(supplier_key, consumer_key)

    def orphan(self, key):
        if not self.is_orphan(key):
            self.creators[key] = "vacuum:"
            self.nodes[key].orphan(self)
            for product_key in self.get_products(key):
                self.orphan(product_key)

    def clean(self):
        """Remove all orphaned nodes except those that supply to other non-orphaned nodes."""
        cleaned_some = True
        while cleaned_some:
            cleaned_some = False
            for orphan_key in sorted(self.products.get("vacuum:", [])):
                consumer_keys = self.consumers.get(orphan_key)
                if consumer_keys is None:
                    cleaned_some = True
                    node = self.nodes.pop(orphan_key)
                    self.kinds.inverse.discard(orphan_key)
                    node.cleanup(self)
                    self.products.discard(orphan_key)
                    self.creators.discard(orphan_key)
                    self.consumers.discard(orphan_key)
                    self.suppliers.discard(orphan_key)
