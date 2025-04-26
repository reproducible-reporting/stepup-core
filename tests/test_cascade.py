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
"""Unit tests for stepup.core.cascade."""

import sqlite3
from collections.abc import Iterator

import attrs
import pytest

from stepup.core.cascade import Cascade, Node, Root
from stepup.core.exceptions import CyclicError, GraphError


@pytest.fixture
def cascade():
    con = sqlite3.Connection(":memory:")
    return Cascade(con)


FROM_SCRATCH_FORMAT_STR = """\
root:
"""


def test_from_scratch(cascade):
    assert cascade.root.i == 1
    assert cascade.root.creator() == cascade.root
    assert cascade.root.creator_orphan() == (cascade.root, False)
    with pytest.raises(attrs.exceptions.FrozenInstanceError):
        cascade.root.i = 3
    assert cascade.format_str() == FROM_SCRATCH_FORMAT_STR
    assert list(cascade.nodes()) == [Root(cascade, 1, "")]


def test_supply_missing_nodes(cascade):
    fake3 = Node(cascade, 3, "fake3")
    fake4 = Node(cascade, 4, "fake3")
    with pytest.raises(GraphError):
        fake3.add_supplier(cascade.root)
    with pytest.raises(GraphError):
        cascade.root.add_supplier(fake4)
    with pytest.raises(GraphError):
        fake3.add_supplier(fake4)


def test_no_node_class(cascade):
    with pytest.raises(TypeError):
        cascade.create(int, cascade.root, "bla")


def test_check_consistency_root1(cascade):
    # Manually set creator field of root node to None
    cascade.con.execute("UPDATE node SET creator = NULL WHERE i = 1")
    with pytest.raises(GraphError):
        cascade.check_consistency()


def test_check_consistency_root2(cascade):
    # Manually set root to orphan
    cascade.con.execute("UPDATE node SET orphan = TRUE WHERE i = 1")
    with pytest.raises(GraphError):
        cascade.check_consistency()


FOO_SCHEMA = """
CREATE TABLE IF NOT EXISTS log (msg TEXT);
CREATE TABLE IF NOT EXISTS foo (
    node INTEGER PRIMARY KEY,
    value INTEGER,
    FOREIGN KEY (node) REFERENCES node(i)
) WITHOUT ROWID;
"""


@attrs.define
class Foo(Node):
    @classmethod
    def kind(cls) -> str:
        """Lower-case prefix of the key string representing a node."""
        return "f"

    @classmethod
    def schema(cls) -> str | None:
        """Return node-specific SQL commands to initialize the database."""
        return FOO_SCHEMA

    def initialize(self, value: int | None = None):
        """Create extra information in the database about this node."""
        if value is not None:
            self.con.execute(
                "INSERT INTO foo VALUES(:node, :value) ON CONFLICT "
                "DO UPDATE SET value = :value WHERE node = :node",
                {"node": self.i, "value": value},
            )
        self.con.execute("INSERT INTO log VALUES(?)", (f"init {self.key()}",))

    def validate(self):
        """Validate extra information about this node is present in the database."""
        row = self.con.execute("SELECT 1 FROM foo WHERE node = ?", (self.i,)).fetchone()
        if row is None:
            raise ValueError(f"Foo node {self.key()} has no value.")

    def format_properties(self) -> Iterator[tuple[str, str]]:
        """Iterate over key-value pairs that represent the properties of the node."""
        row = self.con.execute("SELECT value FROM foo WHERE node = ?", (self.i,)).fetchone()
        yield "value", str(row[0])

    def _make_orphan(self):
        """Update the node or the graph when this node loses its creator node."""
        self.con.execute("INSERT INTO log VALUES(?)", (f"make orphan {self.key()}",))

    def _undo_orphan(self):
        """Update the node or the graph when this node loses its creator node."""
        self.con.execute("INSERT INTO log VALUES(?)", (f"undo orphan {self.key()}",))

    def detach(self):
        """Detach this node from the graph."""
        self.con.execute("INSERT INTO log VALUES(?)", (f"detach {self.key()}",))

    def clean(self):
        """Perform a cleanup right before the orphaned node is removed from the graph."""
        self.con.execute("DELETE FROM foo WHERE node = ?", (self.i,))
        self.con.execute("INSERT INTO log VALUES(?)", (f"clean {self.key()}",))

    def act(self, message: str):
        self.con.execute("INSERT INTO log VALUES(?)", (f"act {self.key()} {message}",))

    def get_value(self) -> int:
        """Return the value associated with this Foo node."""
        row = self.con.execute("SELECT value FROM foo where node = ?", (self.i,)).fetchone()
        return row[0]


@attrs.define(eq=False)
class LogCascade(Cascade):
    @staticmethod
    def default_node_classes() -> list[type[Node]]:
        return [*Cascade.default_node_classes(), Foo]

    @property
    def application_id(self) -> int:
        return 768739999

    @property
    def schema_version(self) -> int:
        return 1


@pytest.fixture
def lc():
    con = sqlite3.Connection(":memory:")
    return LogCascade(con)


SINGLETON1_FORMAT_STR = """\
root:
             creates   f:one

f:one
               value = 1
          created by   root:
"""


SINGLETON2_FORMAT_STR = """\
root:

(f:one)
               value = 1
"""


SINGLETON3_FORMAT_STR = """\
root:
"""


def test_singleton(lc):
    assert lc.root.creator() == lc.root
    assert lc.root.creator_orphan() == (lc.root, False)
    foo = lc.create(Foo, lc.root, "one", value=1)
    assert foo.key() == "f:one"
    assert foo.i == 2
    assert foo.label == "one"
    foo.act("hello")
    assert len(list(foo.consumers())) == 0
    assert len(list(foo.suppliers())) == 0
    assert len(list(foo.products())) == 0
    assert foo.creator() == lc.root
    assert foo.creator_orphan() == (lc.root, False)
    assert lc.format_str() == SINGLETON1_FORMAT_STR
    foo.orphan()
    assert foo.is_orphan()
    assert foo.creator() is None
    assert foo.creator_orphan() == (None, None)
    assert lc.format_str() == SINGLETON2_FORMAT_STR
    foo = lc.create(Foo, lc.root, "one")
    assert lc.format_str() == SINGLETON1_FORMAT_STR
    foo.orphan()
    foo = lc.create(Foo, lc.root, "one", value=-1)
    assert foo.get_value() == -1
    foo.orphan()
    lc.clean()
    rows = lc.con.execute("SELECT msg FROM log").fetchall()
    msgs = [row[0] for row in rows]
    assert msgs == [
        "init f:one",
        "act f:one hello",
        "make orphan f:one",
        "init f:one",
        "make orphan f:one",
        "init f:one",
        "make orphan f:one",
        "clean f:one",
    ]
    assert lc.format_str() == SINGLETON3_FORMAT_STR

    # Validate sanity checking
    with pytest.raises(TypeError):
        lc.create(int, lc.root, "one", value=123)
    with pytest.raises(TypeError):
        lc.create("Foo", lc.root, "one", value=123)
    lc.create(Foo, lc.root, "one", value=123)
    with pytest.raises(GraphError):
        lc.create(Foo, lc.root, "one", value=1234)


CHAIN1_FORMAT_STR = """\
root:
             creates   f:one
             creates   f:zero

f:zero
               value = 0
          created by   root:
             creates   f:four
            supplies   (f:two)

f:one
               value = 1
          created by   root:
            supplies   (f:two)

(f:two)
               value = 2
            consumes   f:one
            consumes   f:zero
             creates   (f:three)
            supplies   f:four

(f:three)
               value = 3
          created by   (f:two)

f:four
               value = 4
          created by   f:zero
            consumes   (f:two)
"""


CHAIN2_FORMAT_STR = """\
root:
             creates   f:one

(f:zero)
               value = 0
             creates   (f:four)
            supplies   f:two

f:one
               value = 1
          created by   root:
             creates   f:two
            supplies   f:two

f:two
               value = 2
          created by   f:one
            consumes   (f:zero)
            consumes   f:one
             creates   f:three
            supplies   (f:four)

f:three
               value = 3
          created by   f:two

(f:four)
               value = 4
          created by   (f:zero)
            consumes   f:two
"""


def test_chain(lc):
    # Prepare cascade object for testing
    # root +-> foo0 --> foo4
    #      +-> foo1
    #      +-> foo2 --> foo3
    foo0 = lc.create(Foo, lc.root, "zero", value=0)
    foo1 = lc.create(Foo, lc.root, "one", value=1)
    foo2 = lc.create(Foo, lc.root, "two", value=2)
    foo3 = lc.create(Foo, foo2, "three", value=3)
    foo4 = lc.create(Foo, foo0, "four", value=4)
    assert foo0.key() == "f:zero"
    assert foo1.key() == "f:one"
    assert foo2.key() == "f:two"
    assert foo3.key() == "f:three"
    assert foo4.key() == "f:four"
    foo2.add_supplier(foo0)
    foo2.add_supplier(foo1)
    foo4.add_supplier(foo2)

    # Test nodes
    assert list(lc.nodes()) == [lc.root, foo0, foo1, foo2, foo3, foo4]
    assert set(lc.nodes(Foo)) == {foo0, foo1, foo2, foo3, foo4}

    # Test creator
    assert foo3.creator() == foo2
    assert foo3.creator_orphan() == (foo2, False)
    assert foo4.creator() == foo0
    assert foo4.creator_orphan() == (foo0, False)

    # Test products
    assert list(foo2.products()) == [foo3]
    assert list(foo0.products()) == [foo4]

    # Test consumers
    assert list(foo0.consumers()) == [foo2]
    assert list(foo1.consumers()) == [foo2]
    assert list(foo2.consumers()) == [foo4]
    assert list(foo3.consumers()) == []
    assert list(foo4.consumers()) == []

    # Test suppliers
    assert list(foo0.suppliers()) == []
    assert list(foo1.suppliers()) == []
    assert list(foo2.suppliers()) == [foo0, foo1]
    assert list(foo3.suppliers()) == []
    assert list(foo4.suppliers()) == [foo2]

    # Modify the graph and test result
    foo2.orphan()
    assert foo2.is_orphan()
    assert foo3.is_orphan()
    assert lc.format_str() == CHAIN1_FORMAT_STR
    assert set(lc.nodes(Foo)) == {foo0, foo1, foo4}
    assert set(lc.nodes(Foo, include_orphans=True)) == {foo0, foo1, foo2, foo3, foo4}

    # foo0
    assert list(foo0.consumers()) == []
    assert list(foo0.suppliers()) == []
    assert foo0.creator() == lc.root
    assert list(foo0.products()) == [foo4]
    # foo1
    assert list(foo1.consumers()) == []
    assert list(foo1.suppliers()) == []
    assert foo1.creator() == lc.root
    assert list(foo1.products()) == []
    # foo2
    assert list(foo2.consumers()) == [foo4]
    assert list(foo2.suppliers()) == [foo0, foo1]
    assert foo2.is_orphan()
    assert foo2.creator() is None
    assert foo2.creator_orphan() == (None, None)
    assert list(foo2.products()) == [foo3]
    # foo3
    assert list(foo3.consumers()) == []
    assert list(foo3.suppliers()) == []
    assert foo3.is_orphan()
    assert foo3.creator() == foo2
    assert foo3.creator_orphan() == (foo2, True)
    assert list(foo3.products()) == []
    # foo4
    assert list(foo4.consumers()) == []
    assert list(foo4.suppliers()) == []
    assert foo4.creator() == foo0
    assert foo4.creator_orphan() == (foo0, False)
    assert list(foo4.products()) == []

    # Orphan, recreate, clean, and check log messages
    foo0.orphan()
    foo2.recreate(foo1)
    assert lc.format_str() == CHAIN2_FORMAT_STR
    lc.clean()
    rows = lc.con.execute("SELECT msg FROM log").fetchall()
    msgs = [row[0] for row in rows]
    assert msgs == [
        "init f:zero",
        "init f:one",
        "init f:two",
        "init f:three",
        "init f:four",
        "make orphan f:two",
        "make orphan f:three",
        "make orphan f:zero",
        "make orphan f:four",
        "undo orphan f:two",
        "undo orphan f:three",
        "clean f:four",
    ]


CLEAN_CONSUMER1_FORMAT_STR = """\
root:
             creates   f:3

(f:0)
               value = 0
             creates   (f:1)
             creates   (f:2)

(f:1)
               value = 1
          created by   (f:0)
            supplies   f:3

(f:2)
               value = 2
          created by   (f:0)
            supplies   f:3

f:3
               value = 3
          created by   root:
            consumes   (f:1)
            consumes   (f:2)
"""


def test_clean_consumers(lc):
    """Orphan nodes only get removed when they have no consumers."""
    foo0 = lc.create(Foo, lc.root, "0", value=0)
    foo1 = lc.create(Foo, foo0, "1", value=1)
    foo2 = lc.create(Foo, foo0, "2", value=2)
    foo3 = lc.create(Foo, lc.root, "3", value=3)
    foo3.add_supplier(foo1)
    foo3.add_supplier(foo2)
    foo0.orphan()
    assert lc.format_str() == CLEAN_CONSUMER1_FORMAT_STR
    lc.clean()
    assert list(lc.nodes(Foo, include_orphans=True)) == [foo0, foo1, foo2, foo3]
    assert lc.format_str() == CLEAN_CONSUMER1_FORMAT_STR
    foo3.orphan()
    lc.clean()
    assert len(list(lc.nodes(Foo, include_orphans=True))) == 0
    assert lc.format_str() == FROM_SCRATCH_FORMAT_STR


def test_recycle_consumers(lc):
    """When an orphan node with consumers is recycled, the consumer relations remain."""
    foo0 = lc.create(Foo, lc.root, "0", value=0)
    foo1 = lc.create(Foo, lc.root, "1", value=1)
    foo2 = lc.create(Foo, lc.root, "2", value=2)
    foo1.add_supplier(foo0)
    foo2.add_supplier(foo0)
    assert list(foo0.consumers()) == [foo1, foo2]
    foo0.orphan()
    assert list(foo0.consumers()) == [foo1, foo2]
    lc.create(Foo, lc.root, "0")
    assert list(foo0.consumers()) == [foo1, foo2]


def test_clean_nested(lc):
    foo0 = lc.create(Foo, None, "0", value=0)
    foo1 = lc.create(Foo, None, "1", value=1)
    foo2 = lc.create(Foo, None, "2", value=2)
    foo3 = lc.create(Foo, lc.root, "3", value=3)

    # Test is_alive method
    assert foo0.is_alive()
    assert foo1.is_alive()
    assert foo2.is_alive()
    assert foo3.is_alive()

    foo1.add_supplier(foo0)
    foo2.add_supplier(foo1)
    foo3.add_supplier(foo2)
    lc.clean()
    assert lc.find(Foo, "3") == foo3
    assert lc.find_orphan(Foo, "3") == (foo3, False)
    assert lc.find(Foo, "0") == foo0
    assert lc.find_orphan(Foo, "0") == (foo0, True)
    foo3.orphan()
    assert lc.find(Foo, "3") == foo3
    assert lc.find_orphan(Foo, "3") == (foo3, True)
    assert lc.find(Foo, "0") == foo0
    assert lc.find_orphan(Foo, "0") == (foo0, True)
    lc.clean()
    assert lc.find(Foo, "3") is None
    assert lc.find_orphan(Foo, "3") == (None, None)
    assert lc.find(Foo, "0") is None
    assert lc.find_orphan(Foo, "0") == (None, None)

    # Test is_alive method
    assert not foo0.is_alive()
    assert not foo1.is_alive()
    assert not foo2.is_alive()
    assert not foo3.is_alive()


def test_create_orphan(lc):
    foo1 = lc.create(Foo, None, "some", value=0)
    assert foo1.is_orphan()
    assert foo1.get_value() == 0
    foo2 = lc.create(Foo, None, "some", value=10)
    assert foo1 == foo2
    assert foo1.is_orphan()
    assert foo2.is_orphan()
    assert foo1.get_value() == 10
    assert foo2.get_value() == 10


def test_create_orphan_with_products_a(lc):
    foo0 = lc.create(Foo, None, "0", value=0)
    foo1 = lc.create(Foo, foo0, "1", value=1)
    assert foo0.is_orphan()
    assert foo1.is_orphan()
    foo0_bis = lc.create(Foo, None, "0", value=0)
    assert foo0 == foo0_bis
    assert foo0.is_orphan()
    assert foo0_bis.is_orphan()
    assert foo1.creator() is None


def test_create_orphan_with_products_b(lc):
    foo0 = lc.create(Foo, None, "0", value=0)
    foo1 = lc.create(Foo, foo0, "1", value=1)
    assert foo0.is_orphan()
    assert foo1.is_orphan()
    foo0_bis = lc.create(Foo, lc.root, "0", value=0)
    assert foo0 == foo0_bis
    assert not foo0.is_orphan()
    assert not foo0_bis.is_orphan()
    assert foo1.creator() is None


def test_create_orphan_try_cyclic(lc):
    foo0 = lc.create(Foo, None, "0", value=0)
    foo1 = lc.create(Foo, foo0, "1", value=1)
    assert foo0.is_orphan()
    assert foo1.is_orphan()
    foo0_bis = lc.create(Foo, foo1, "0", value=0)
    assert foo0.is_orphan()
    assert foo1.is_orphan()
    assert foo0_bis.is_orphan()
    assert foo1.creator() is None


def test_duplicate_dependency(lc):
    foo0 = lc.create(Foo, lc.root, "0", value=0)
    foo1 = lc.create(Foo, foo0, "1", value=1)
    foo0.add_supplier(foo1)
    with pytest.raises(GraphError):
        foo0.add_supplier(foo1)


def test_cyclic1(lc):
    foo0 = lc.create(Foo, lc.root, "0", value=0)
    foo1 = lc.create(Foo, foo0, "1", value=1)
    foo0.add_supplier(foo1)
    assert list(foo0.suppliers()) == [foo1]


def test_cyclic2(lc):
    foo0 = lc.create(Foo, lc.root, "0", value=0)
    foo1 = lc.create(Foo, lc.root, "1", value=1)
    foo1.add_supplier(foo0)
    with pytest.raises(CyclicError):
        foo0.add_supplier(foo1)


def test_cyclic3(lc):
    foo0 = lc.create(Foo, lc.root, "0", value=0)
    foo1 = lc.create(Foo, lc.root, "1", value=1)
    foo2 = lc.create(Foo, lc.root, "2", value=2)
    foo1.add_supplier(foo0)
    foo2.add_supplier(foo1)
    with pytest.raises(CyclicError):
        foo0.add_supplier(foo2)


def test_walk_consumers(lc):
    # foo0 --> foo1 --> foo2
    #      --> foo3
    foo0 = lc.create(Foo, lc.root, "0", value=0)
    foo1 = lc.create(Foo, lc.root, "1", value=1)
    foo2 = lc.create(Foo, lc.root, "2", value=2)
    foo3 = lc.create(Foo, lc.root, "3", value=3)
    foo1.add_supplier(foo0)
    foo2.add_supplier(foo1)
    foo3.add_supplier(foo0)
    assert set(lc.walk_consumers([foo0.i])) == {foo0.i, foo1.i, foo2.i, foo3.i}
    assert set(lc.walk_consumers([foo1.i])) == {foo1.i, foo2.i}
    assert set(lc.walk_consumers([foo3.i])) == {foo3.i}
    assert set(lc.walk_consumers([foo1.i, foo3.i])) == {foo1.i, foo3.i, foo2.i}


def test_load_existing(path_tmp):
    con1 = sqlite3.Connection(path_tmp / "lc.db")
    with con1:
        lc = LogCascade(con1)
        graph = lc.format_str()
    con1.close()

    con2 = sqlite3.Connection(path_tmp / "lc.db")
    with con2:
        lc = LogCascade(con2)
        assert lc.format_str() == graph
    con2.close()


def test_relocate_tree(lc):
    # Create a LogCasecade with the following topology:
    # foo0 +-> foo1 +-> foo2
    #      |        +-> foo3
    #      +-> foo4
    foo0 = lc.create(Foo, lc.root, "0", value=0)
    foo1 = lc.create(Foo, foo0, "1", value=1)
    foo2 = lc.create(Foo, foo1, "2", value=2)
    lc.create(Foo, foo1, "3", value=3)
    foo4 = lc.create(Foo, foo0, "4", value=4)
    foo2.add_supplier(foo1)
    foo2.add_supplier(foo4)

    # Orphan the foo1 node and attach it to the foo4 node.
    # The new topology is:
    # foo0 --> foo4 --> foo1 +-> foo2
    #                        +-> foo3
    foo1.orphan()
    foo1.recreate(foo4)
    assert lc.format_str() == RELOCATE_FORMAT_STR


RELOCATE_FORMAT_STR = """\
root:
             creates   f:0

f:0
               value = 0
          created by   root:
             creates   f:4

f:1
               value = 1
          created by   f:4
             creates   f:2
             creates   f:3
            supplies   f:2

f:2
               value = 2
          created by   f:1
            consumes   f:1
            consumes   f:4

f:3
               value = 3
          created by   f:1

f:4
               value = 4
          created by   f:0
             creates   f:1
            supplies   f:2
"""


def test_check_consistency_creator(lc):
    # Manually set creator field of foo0 node to None.
    foo0 = lc.create(Foo, lc.root, "0", value=0)
    lc.con.execute("UPDATE node SET creator = NULL WHERE i = ?", (foo0.i,))
    with pytest.raises(GraphError):
        lc.check_consistency()


def test_check_consistency_orphan(lc):
    # Manually make foo0 an orphan while it has a creator.
    foo0 = lc.create(Foo, lc.root, "0", value=0)
    lc.con.execute("UPDATE node SET orphan = TRUE WHERE i = ?", (foo0.i,))
    with pytest.raises(GraphError):
        lc.check_consistency()


def test_check_consistency_second_root(lc):
    # Manually make foo0 its own creator
    foo0 = lc.create(Foo, lc.root, "0", value=0)
    lc.con.execute("UPDATE node SET creator = ? WHERE i = ?", (foo0.i, foo0.i))
    with pytest.raises(GraphError):
        lc.check_consistency()


RELOCATE_NESTED_FORMAT_STR = """\
root:
             creates   f:0

f:0
               value = 0
          created by   root:
             creates   f:4

(f:1)
               value = 1
             creates   (f:3)
            supplies   f:2

f:2
               value = 2
          created by   f:4
            consumes   (f:1)
            consumes   f:4

(f:3)
               value = 3
          created by   (f:1)

f:4
               value = 4
          created by   f:0
             creates   f:2
            supplies   f:2
"""


def test_relocate_nested_orphan(lc):
    # Create a LogCasecade with the following topology:
    # foo0 +-> foo1 +-> foo2
    #      |        +-> foo3
    #      +-> foo4
    foo0 = lc.create(Foo, lc.root, "0", value=0)
    foo1 = lc.create(Foo, foo0, "1", value=1)
    foo2 = lc.create(Foo, foo1, "2", value=2)
    lc.create(Foo, foo1, "3", value=3)
    foo4 = lc.create(Foo, foo0, "4", value=4)
    foo2.add_supplier(foo1)
    foo2.add_supplier(foo4)

    # Orphan the foo1 node and attach foo2 to foo4.
    # The new topology is:
    # foo0 --> foo4 --> foo2
    foo1.orphan()
    foo2.recreate(foo4)
    print(lc.format_str())
    assert lc.format_str() == RELOCATE_NESTED_FORMAT_STR

    lc.clean()
    rows = lc.con.execute("SELECT msg FROM log").fetchall()
    msgs = [row[0] for row in rows]
    assert msgs == [
        "init f:0",
        "init f:1",
        "init f:2",
        "init f:3",
        "init f:4",
        "make orphan f:1",
        "make orphan f:2",
        "make orphan f:3",
        "detach f:1",
        "undo orphan f:2",
        "clean f:3",
    ]
