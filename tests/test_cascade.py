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
"""Unit tests for stepup.core.cascade."""

import tempfile
import typing

import attrs
import msgpack
import pytest
from path import Path

from stepup.core.cascade import Cascade, Node, Root, Vacuum
from stepup.core.exceptions import GraphError


def check_cascade_unstructure(cascade: Cascade) -> Cascade:
    state = cascade.unstructure()
    data = msgpack.packb(state)
    cls = cascade.__class__
    cascade2 = cls.structure(msgpack.unpackb(data))
    assert cascade2.unstructure() == state
    if "root:" in cascade.nodes:
        with tempfile.TemporaryDirectory("stepup-pytest") as dn:
            path = Path(dn) / "cascade.mpk"
            cascade.to_file(path)
            cascade3 = cls.from_file(path)
        assert cascade3.unstructure() == state
        return cascade3
    return cascade2


def test_empty():
    cascade = Cascade()
    assert cascade.node_classes == {"root": Root, "vacuum": Vacuum}
    assert cascade.unstructure() == {"nodes": [], "products": [], "consumers": []}
    assert cascade.get_nodes() == []


FROM_SCRATCH_FORMAT_STR = """\
root:
             version = v1
"""


def test_from_scratch():
    cascade = Cascade.from_scratch()
    assert cascade.node_classes == {"root": Root, "vacuum": Vacuum}
    expected = {
        "nodes": [{"c": "root", "v": "v1"}, {"c": "vacuum"}],
        "products": [[0, 0], [0, 1]],
        "consumers": [],
    }
    assert cascade.unstructure() == expected
    check_cascade_unstructure(cascade)
    root = cascade.get_root()
    assert root.version == "v1"
    assert cascade.format_str() == FROM_SCRATCH_FORMAT_STR
    assert cascade.get_nodes() == [cascade.nodes["root:"], cascade.nodes["vacuum:"]]
    assert not cascade.is_orphan("root:")
    assert not cascade.is_orphan("vacuum:")


def test_no_node_class():
    cascade = Cascade()
    with pytest.raises(KeyError):
        cascade.supply("foo:1", "foo:2")
    with pytest.raises(TypeError):
        cascade.create(Foo("bar"), None)


@attrs.define
class Foo(Node):
    _name: str = attrs.field()
    other: int = attrs.field(default=5)

    @classmethod
    def key_tail(cls, data: dict[str, typing.Any], strings: list[str] | None = None) -> str:
        return data.get("_name", data.get("n"))

    @property
    def name(self):
        return self._name

    @classmethod
    def structure(cls, cascade: "LogCascade", strings: list[str], data: dict) -> typing.Self:
        kwargs = {"name": data["n"]}
        if "o" in data:
            kwargs["other"] = data["o"]
        return cls(**kwargs)

    def unstructure(self, cascade: "LogCascade", lookup: dict[str, int]) -> dict:
        data = {"n": self._name}
        if self.other != 5:
            data["o"] = self.other
        return data

    def format_properties(self, cascade: "LogCascade") -> typing.Iterator[tuple[str, str]]:
        yield "name", self._name
        if self.other != 5:
            yield "other", str(self.other)

    def recycle(self, cascade: "LogCascade", old: "Foo"):
        if old is None:
            cascade.log.append(f"new {self.key}")
        else:
            cascade.log.append(f"recycle {self.key}")

    def orphan(self, cascade: "LogCascade"):
        cascade.log.append(f"orphan {self.key}")

    def cleanup(self, cascade: "LogCascade"):
        cascade.log.append(f"clean {self.key}")

    def act(self, cascade: "LogCascade", message: str):
        cascade.log.append(f"act {self.key} {message}")


@attrs.define
class LogCascade(Cascade):
    log: list[str] = attrs.field(init=False, factory=list)

    @staticmethod
    def default_node_classes() -> dict[str, type[Node]]:
        result = Cascade.default_node_classes()
        result["foo"] = Foo
        return result


SINGLETON1_FORMAT_STR = """\
root:
             version = v1
             creates   foo:one

foo:one
                name = one
          created by   root:
"""


SINGLETON2_FORMAT_STR = """\
root:
             version = v1
"""


def test_singleton():
    cascade = LogCascade.from_scratch()
    assert cascade.node_classes == {"root": Root, "vacuum": Vacuum, "foo": Foo}
    check_cascade_unstructure(cascade)
    foo = Foo("one")
    key = cascade.create(foo, "root:")
    assert key == "foo:one"
    assert cascade.unstructure() == {
        "nodes": [{"c": "root", "v": "v1"}, {"c": "vacuum"}, {"c": "foo", "n": "one"}],
        "products": [[0, 0], [0, 1], [0, 2]],
        "consumers": [],
    }
    check_cascade_unstructure(cascade)
    assert foo.name == "one"
    with pytest.raises(AttributeError):
        foo.name = "new"
    foo.act(cascade, "hello")
    assert cascade.get_consumers(key) == []
    assert cascade.get_suppliers(key) == []
    assert cascade.get_creator(key) == "root:"
    assert cascade.get_products(key) == []
    assert cascade.format_str() == SINGLETON1_FORMAT_STR
    cascade.orphan("foo:one")
    check_cascade_unstructure(cascade)
    cascade.clean()
    check_cascade_unstructure(cascade)
    assert cascade.log == ["new foo:one", "act foo:one hello", "orphan foo:one", "clean foo:one"]
    assert cascade.format_str() == SINGLETON2_FORMAT_STR

    # Validate sanity checking
    foo = Foo("one")
    with pytest.raises(TypeError):
        cascade.create(foo, None)
    cascade.create(foo, "root:")
    with pytest.raises(ValueError):
        cascade.create(foo, "root:")
    with pytest.raises(ValueError):
        cascade.create(Foo("one"), "root:")


CHAIN1_FORMAT_STR = """\
root:
             version = v1
             creates   foo:one
             creates   foo:zero

foo:zero
                name = zero
          created by   root:
             creates   foo:four
            supplies   (foo:two)

foo:one
                name = one
          created by   root:
            supplies   (foo:two)

(foo:two)
                name = two
               other = 2
            consumes   foo:one
            consumes   foo:zero
            supplies   foo:four

(foo:three)
                name = three

foo:four
                name = four
          created by   foo:zero
            consumes   (foo:two)
"""


def test_chain():
    # Prepare cascade object for testing
    cascade = LogCascade.from_scratch()
    foo0 = Foo("zero")
    foo1 = Foo("one")
    foo2 = Foo("two", other=2)
    foo3 = Foo("three")
    foo4 = Foo("four", other=5)
    key0 = cascade.create(foo0, "root:")
    key1 = cascade.create(foo1, "root:")
    key2 = cascade.create(foo2, "root:")
    key3 = cascade.create(foo3, key2)
    key4 = cascade.create(foo4, key0)
    assert key0 == "foo:zero"
    assert key1 == "foo:one"
    assert key2 == "foo:two"
    assert key3 == "foo:three"
    assert key4 == "foo:four"
    cascade.supply(key0, key2)
    cascade.supply(key1, key2)
    cascade.supply(key2, key4)
    check_cascade_unstructure(cascade)
    assert cascade.get_nodes() == [
        foo4,
        foo1,
        foo3,
        foo2,
        foo0,
        cascade.nodes["root:"],
        cascade.nodes["vacuum:"],
    ]
    assert cascade.get_nodes(kind="foo") == [foo4, foo1, foo3, foo2, foo0]

    # Directly test getter methods
    assert cascade.get_creator(key3) == key2
    assert cascade.get_creator(key4) == key0
    assert cascade.get_products(key2) == [key3]
    assert cascade.get_products(key0) == [key4]
    assert cascade.get_consumers(key0) == [key2]
    assert cascade.get_consumers(key1) == [key2]
    assert cascade.get_consumers(key2) == [key4]
    assert cascade.get_consumers(key3) == []
    assert cascade.get_consumers(key4) == []
    assert cascade.get_suppliers(key0) == []
    assert cascade.get_suppliers(key1) == []
    assert cascade.get_suppliers(key2) == [key1, key0]
    assert cascade.get_suppliers(key3) == []
    assert cascade.get_suppliers(key4) == [key2]
    assert not cascade.is_orphan("foo:two")
    assert not cascade.is_orphan("foo:three")

    cascade.orphan("foo:two")
    assert cascade.is_orphan("foo:two")
    assert cascade.is_orphan("foo:three")
    check_cascade_unstructure(cascade)
    assert cascade.unstructure() == {
        "nodes": [
            {"c": "root", "v": "v1"},
            {"c": "vacuum"},
            {"c": "foo", "n": "zero"},
            {"c": "foo", "n": "one"},
            {"c": "foo", "n": "two", "o": 2},
            {"c": "foo", "n": "three"},
            {"c": "foo", "n": "four"},
        ],
        "products": [[0, 0], [0, 1], [0, 2], [0, 3], [1, 4], [1, 5], [2, 6]],
        "consumers": [[2, 4], [3, 4], [4, 6]],
    }
    assert cascade.format_str() == CHAIN1_FORMAT_STR
    assert cascade.get_nodes(kind="foo") == [foo4, foo1, foo0]
    assert cascade.get_nodes(kind="foo", include_orphans=True) == [foo4, foo1, foo3, foo2, foo0]

    # key0
    assert cascade.get_consumers(key0) == []
    assert cascade.get_suppliers(key0) == []
    assert cascade.get_creator(key0) == "root:"
    assert cascade.get_products(key0) == ["foo:four"]
    # key1
    assert cascade.get_consumers(key1) == []
    assert cascade.get_suppliers(key1) == []
    assert cascade.get_creator(key1) == "root:"
    assert cascade.get_products(key1) == []
    # key2
    assert cascade.get_consumers(key2) == ["foo:four"]
    assert cascade.get_suppliers(key2) == ["foo:one", "foo:zero"]
    assert cascade.get_creator(key2) == "vacuum:"
    assert cascade.get_products(key2) == []
    # key3
    assert cascade.get_consumers(key3) == []
    assert cascade.get_suppliers(key3) == []
    assert cascade.get_creator(key3) == "vacuum:"
    assert cascade.get_products(key3) == []
    # key4
    assert cascade.get_consumers(key4) == []
    assert cascade.get_suppliers(key4) == []
    assert cascade.get_creator(key4) == "foo:zero"
    assert cascade.get_products(key4) == []
    cascade.orphan("foo:zero")
    check_cascade_unstructure(cascade)
    cascade.clean()
    check_cascade_unstructure(cascade)
    assert cascade.unstructure() == {
        "nodes": [{"c": "root", "v": "v1"}, {"c": "vacuum"}, {"c": "foo", "n": "one"}],
        "products": [[0, 0], [0, 1], [0, 2]],
        "consumers": [],
    }
    assert cascade.log == [
        "new foo:zero",
        "new foo:one",
        "new foo:two",
        "new foo:three",
        "new foo:four",
        "orphan foo:two",
        "orphan foo:three",
        "orphan foo:zero",
        "orphan foo:four",
        "clean foo:four",
        "clean foo:three",
        "clean foo:two",
        "clean foo:zero",
    ]


CLEAN_CONSUMER1_FORMAT_STR = """\
root:
             version = v1
             creates   foo:3

(foo:0)
                name = 0

(foo:1)
                name = 1
            supplies   foo:3

(foo:2)
                name = 2
            supplies   foo:3

foo:3
                name = 3
          created by   root:
            consumes   (foo:1)
            consumes   (foo:2)
"""


CLEAN_CONSUMER2_FORMAT_STR = """\
root:
             version = v1
             creates   foo:3

(foo:1)
                name = 1
            supplies   foo:3

(foo:2)
                name = 2
            supplies   foo:3

foo:3
                name = 3
          created by   root:
            consumes   (foo:1)
            consumes   (foo:2)
"""


def test_clean_consumers():
    """Orphan nodes only get removed when they have no consumers."""
    cascade = LogCascade.from_scratch()
    cascade.create(Foo("0"), "root:")
    cascade.create(Foo("1"), "foo:0")
    cascade.create(Foo("2"), "foo:0")
    cascade.create(Foo("3"), "root:")
    cascade.supply("foo:1", "foo:3")
    cascade.supply("foo:2", "foo:3")
    cascade.orphan("foo:0")
    assert cascade.format_str() == CLEAN_CONSUMER1_FORMAT_STR
    cascade.clean()
    assert "foo:0" not in cascade.nodes
    assert "foo:1" in cascade.nodes
    assert "foo:2" in cascade.nodes
    assert "foo:3" in cascade.nodes
    assert cascade.format_str() == CLEAN_CONSUMER2_FORMAT_STR
    cascade.orphan("foo:3")
    cascade.clean()
    assert "foo:0" not in cascade.nodes
    assert "foo:3" not in cascade.nodes
    assert "foo:1" not in cascade.nodes
    assert "foo:2" not in cascade.nodes
    assert cascade.format_str() == FROM_SCRATCH_FORMAT_STR


def test_recycle_consumers():
    """When an orphan node with consumers is recycled, the consumer relations remain."""
    cascade = LogCascade.from_scratch()
    cascade.create(Foo("0", other=10), "root:")
    cascade.create(Foo("1"), "root:")
    cascade.create(Foo("2"), "root:")
    cascade.supply("foo:0", "foo:1")
    cascade.supply("foo:0", "foo:2")
    assert cascade.get_consumers("foo:0") == ["foo:1", "foo:2"]
    assert cascade.nodes["foo:0"].other == 10
    cascade.orphan("foo:0")
    assert cascade.get_consumers("foo:0") == ["foo:1", "foo:2"]
    assert cascade.nodes["foo:0"].other == 10
    cascade.create(Foo("0", other=8), "root:")
    assert cascade.nodes["foo:0"].other == 8
    assert cascade.get_consumers("foo:0") == ["foo:1", "foo:2"]


def test_clean_nested():
    cascade = LogCascade.from_scratch()
    cascade.create(Foo("0"), "vacuum:")
    cascade.create(Foo("1"), "vacuum:")
    cascade.create(Foo("2"), "vacuum:")
    cascade.create(Foo("3"), "root:")
    cascade.supply("foo:0", "foo:1")
    cascade.supply("foo:1", "foo:2")
    cascade.supply("foo:2", "foo:3")
    cascade.clean()
    assert "foo:0" in cascade.nodes
    cascade.orphan("foo:3")
    cascade.clean()
    assert "foo:0" not in cascade.nodes


def test_cyclic1():
    cascade = LogCascade.from_scratch()
    cascade.create(Foo("0"), "vacuum:")
    cascade.create(Foo("1"), "foo:0")
    cascade.supply("foo:1", "foo:0")
    assert "foo:1" in cascade.suppliers["foo:0"]


def test_cyclic2():
    cascade = LogCascade.from_scratch()
    cascade.create(Foo("0"), "root:")
    cascade.create(Foo("1"), "root:")
    cascade.supply("foo:0", "foo:1")
    with pytest.raises(GraphError):
        cascade.supply("foo:1", "foo:0")


def test_walk_consumers():
    cascade = LogCascade.from_scratch()
    cascade.create(Foo("0"), "root:")
    cascade.create(Foo("1"), "root:")
    cascade.create(Foo("2"), "root:")
    cascade.create(Foo("3"), "root:")
    cascade.supply("foo:0", "foo:1")
    cascade.supply("foo:1", "foo:2")
    cascade.supply("foo:1", "foo:3")
    visited = set()
    cascade.walk_consumers("foo:0", visited)
    assert visited == {"foo:0", "foo:1", "foo:2", "foo:3"}
    visited = set()
    cascade.walk_consumers("foo:1", visited)
    assert visited == {"foo:1", "foo:2", "foo:3"}
    visited = set()
    cascade.walk_consumers("foo:3", visited)
    assert visited == {"foo:3"}


def test_walk_suppliers():
    cascade = LogCascade.from_scratch()
    cascade.create(Foo("0"), "root:")
    cascade.create(Foo("1"), "root:")
    cascade.create(Foo("2"), "root:")
    cascade.create(Foo("3"), "root:")
    cascade.supply("foo:0", "foo:1")
    cascade.supply("foo:1", "foo:2")
    cascade.supply("foo:1", "foo:3")
    visited = set()
    cascade.walk_suppliers("foo:0", visited)
    assert visited == {"foo:0"}
    visited = set()
    cascade.walk_suppliers("foo:1", visited)
    assert visited == {"foo:0", "foo:1"}
    visited = set()
    cascade.walk_suppliers("foo:3", visited)
    assert visited == {"foo:0", "foo:1", "foo:3"}
