# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.trellis."""

import sqlite3
from collections.abc import Iterator

import attrs
import pytest
import pytest_asyncio

from stepup.core.exceptions import CyclicError, GraphError
from stepup.core.sqlite3 import DBSession
from stepup.core.trellis import Node, Root, Trellis


@pytest_asyncio.fixture
async def trellis():
    with DBSession.open(":memory:") as db:
        trellis = Trellis(db)
        await trellis.initialize()
        yield trellis


FROM_SCRATCH_FORMAT_STR = """\
root:
"""


async def test_from_scratch(trellis):
    assert trellis.root.i == 1
    async with trellis.db:
        assert trellis.root.creator() == trellis.root
        assert trellis.root.creator_detached() == (trellis.root, False)
        with pytest.raises(attrs.exceptions.FrozenInstanceError):
            trellis.root.i = 3
        assert trellis.format_str() == FROM_SCRATCH_FORMAT_STR
        assert list(trellis.nodes()) == [Root(trellis, 1, "")]


async def test_supply_missing_nodes(trellis):
    async with trellis.db:
        fake3 = Node(trellis, 3, "fake3")
        fake4 = Node(trellis, 4, "fake3")
    with pytest.raises(GraphError):
        async with trellis.db:
            fake3.add_source(trellis.root)
    with pytest.raises(GraphError):
        async with trellis.db:
            trellis.root.add_source(fake4)
    with pytest.raises(GraphError):
        async with trellis.db:
            fake3.add_source(fake4)


async def test_no_node_class(trellis):
    with pytest.raises(TypeError):
        async with trellis.db:
            trellis.create(int, trellis.root, "bla")


async def test_check_consistency_root1(trellis):
    # Manually set creator field of root node to None.
    # This is now rejected by a CHECK constraint, instead of being caught later
    # by _check_consistency().
    async with trellis.db:
        with pytest.raises(sqlite3.IntegrityError):
            trellis.db.execute("UPDATE node SET creator = NULL WHERE i = 1")


async def test_check_consistency_root2(trellis):
    # Manually set root to detached.
    # This is now rejected by a CHECK constraint, instead of being caught later
    # by _check_consistency().
    async with trellis.db:
        with pytest.raises(sqlite3.IntegrityError):
            trellis.db.execute("UPDATE node SET detached = TRUE WHERE i = 1")


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
            self.db.execute(
                "INSERT INTO foo VALUES(:node, :value) ON CONFLICT "
                "DO UPDATE SET value = :value WHERE node = :node",
                {"node": self.i, "value": value},
            )
        self.db.execute("INSERT INTO log VALUES(?)", (f"init {self.key()}",))

    def validate(self):
        """Validate extra information about this node is present in the database."""
        row = self.db.execute("SELECT 1 FROM foo WHERE node = ?", (self.i,)).fetchone()
        if row is None:
            raise ValueError(f"Foo node {self.key()} has no value.")

    def format_properties(self) -> Iterator[tuple[str, str]]:
        """Iterate over key-value pairs that represent the properties of the node."""
        row = self.db.execute("SELECT value FROM foo WHERE node = ?", (self.i,)).fetchone()
        yield "value", str(row[0])

    def give_up(self):
        """Clean up a detached node because it loses a product node."""
        self.db.execute("INSERT INTO log VALUES(?)", (f"give_up {self.key()}",))

    def clean(self):
        """Perform a cleanup right before the detached node is removed from the graph."""
        self.db.execute("DELETE FROM foo WHERE node = ?", (self.i,))
        self.db.execute("INSERT INTO log VALUES(?)", (f"clean {self.key()}",))

    def can_recycle(self, value: int | None = None, **kwargs) -> bool:
        """Decide whether this detached node may be fully recycled by `Trellis.recycle`."""
        return value == self.get_value()

    def update_recycled(self, **kwargs):
        """Update the mutable declared properties of this node after a full recycle."""
        self.db.execute("INSERT INTO log VALUES(?)", (f"update_recycled {self.key()}",))

    def act(self, message: str):
        self.db.execute("INSERT INTO log VALUES(?)", (f"act {self.key()} {message}",))

    def get_value(self) -> int:
        """Return the value associated with this Foo node."""
        row = self.db.execute("SELECT value FROM foo where node = ?", (self.i,)).fetchone()
        return row[0]


@attrs.define(eq=False)
class LogTrellis(Trellis):
    @staticmethod
    def default_node_classes() -> list[type[Node]]:
        return [*Trellis.default_node_classes(), Foo]

    @property
    def application_id(self) -> int:
        return 768739999

    @property
    def schema_version(self) -> int:
        return 1


@pytest_asyncio.fixture
async def lt():
    with DBSession.open(":memory:") as db:
        lt = LogTrellis(db)
        await lt.initialize()
        yield lt


SINGLETON1_FORMAT_STR = """\
root:
             product   f:one

f:one
               value = 1
             creator   root:
"""


SINGLETON2_FORMAT_STR = """\
root:

(f:one)
               value = 1
"""


SINGLETON3_FORMAT_STR = """\
root:
"""


async def test_singleton(lt):
    async with lt.db:
        assert lt.root.creator() == lt.root
        assert lt.root.creator_detached() == (lt.root, False)
        foo = lt.create(Foo, lt.root, "one", value=1)
        assert foo.key() == "f:one"
        assert foo.i == 2
        assert foo.label == "one"
        foo.act("hello")
        assert len(list(foo.sinks())) == 0
        assert len(list(foo.sources())) == 0
        assert len(list(foo.products())) == 0
        assert foo.creator() == lt.root
        assert foo.creator_detached() == (lt.root, False)
        assert lt.format_str() == SINGLETON1_FORMAT_STR
        foo.detach()
        assert foo.is_detached()
        assert foo.creator() is None
        assert foo.creator_detached() == (None, None)
        assert lt.format_str() == SINGLETON2_FORMAT_STR
        foo = lt.create(Foo, lt.root, "one")
        assert lt.format_str() == SINGLETON1_FORMAT_STR
        foo.detach()
        foo = lt.create(Foo, lt.root, "one", value=-1)
        assert foo.get_value() == -1
        foo.detach()
        lt.clean()
        rows = lt.db.execute("SELECT msg FROM log").fetchall()
        msgs = [row[0] for row in rows]
        assert msgs == [
            "init f:one",
            "act f:one hello",
            "init f:one",
            "init f:one",
            "clean f:one",
        ]
        assert lt.format_str() == SINGLETON3_FORMAT_STR

    # Validate sanity checking
    with pytest.raises(TypeError):
        async with lt.db:
            lt.create(int, lt.root, "one", value=123)
    with pytest.raises(TypeError):
        async with lt.db:
            lt.create("Foo", lt.root, "one", value=123)
    async with lt.db:
        lt.create(Foo, lt.root, "one", value=123)
    with pytest.raises(GraphError):
        async with lt.db:
            lt.create(Foo, lt.root, "one", value=1234)


CHAIN1_FORMAT_STR = """\
root:
             product   f:one
             product   f:zero

f:zero
               value = 0
             creator   root:
             product   f:four
                sink   (f:two)

f:one
               value = 1
             creator   root:
                sink   (f:two)

(f:two)
               value = 2
              source   f:one
              source   f:zero
             product   (f:three)
                sink   f:four

(f:three)
               value = 3
             creator   (f:two)

f:four
               value = 4
             creator   f:zero
              source   (f:two)
"""


CHAIN2_FORMAT_STR = """\
root:
             product   f:one

(f:zero)
               value = 0
             product   (f:four)
                sink   f:two

f:one
               value = 1
             creator   root:
             product   f:two
                sink   f:two

f:two
               value = 2
             creator   f:one
              source   f:one
              source   (f:zero)
             product   f:three
                sink   (f:four)

f:three
               value = 3
             creator   f:two

(f:four)
               value = 4
             creator   (f:zero)
              source   f:two
"""


async def test_chain(lt):
    async with lt.db:
        # Prepare trellis object for testing
        # root +-> foo0 --> foo4
        #      +-> foo1
        #      +-> foo2 --> foo3
        foo0 = lt.create(Foo, lt.root, "zero", value=0)
        foo1 = lt.create(Foo, lt.root, "one", value=1)
        foo2 = lt.create(Foo, lt.root, "two", value=2)
        foo3 = lt.create(Foo, foo2, "three", value=3)
        foo4 = lt.create(Foo, foo0, "four", value=4)
        assert foo0.key() == "f:zero"
        assert foo1.key() == "f:one"
        assert foo2.key() == "f:two"
        assert foo3.key() == "f:three"
        assert foo4.key() == "f:four"
        foo2.add_source(foo0)
        foo2.add_source(foo1)
        foo4.add_source(foo2)

        # Test nodes
        assert list(lt.nodes()) == [lt.root, foo0, foo1, foo2, foo3, foo4]
        assert set(lt.nodes(Foo)) == {foo0, foo1, foo2, foo3, foo4}

        # Test creator
        assert foo3.creator() == foo2
        assert foo3.creator_detached() == (foo2, False)
        assert foo4.creator() == foo0
        assert foo4.creator_detached() == (foo0, False)

        # Test products
        assert list(foo2.products()) == [foo3]
        assert list(foo0.products()) == [foo4]

        # Test sinks
        assert list(foo0.sinks()) == [foo2]
        assert list(foo1.sinks()) == [foo2]
        assert list(foo2.sinks()) == [foo4]
        assert list(foo3.sinks()) == []
        assert list(foo4.sinks()) == []

        # Test sources
        assert list(foo0.sources()) == []
        assert list(foo1.sources()) == []
        assert list(foo2.sources()) == [foo0, foo1]
        assert list(foo3.sources()) == []
        assert list(foo4.sources()) == [foo2]

        # Modify the graph and test result
        foo2.detach()
        assert foo2.is_detached()
        assert foo3.is_detached()
        assert lt.format_str() == CHAIN1_FORMAT_STR
        assert set(lt.nodes(Foo)) == {foo0, foo1, foo4}
        assert set(lt.nodes(Foo, include_detached=True)) == {foo0, foo1, foo2, foo3, foo4}

        # foo0
        assert list(foo0.sinks()) == []
        assert list(foo0.sources()) == []
        assert foo0.creator() == lt.root
        assert list(foo0.products()) == [foo4]
        # foo1
        assert list(foo1.sinks()) == []
        assert list(foo1.sources()) == []
        assert foo1.creator() == lt.root
        assert list(foo1.products()) == []
        # foo2
        assert list(foo2.sinks()) == [foo4]
        assert list(foo2.sources()) == [foo0, foo1]
        assert foo2.is_detached()
        assert foo2.creator() is None
        assert foo2.creator_detached() == (None, None)
        assert list(foo2.products()) == [foo3]
        # foo3
        assert list(foo3.sinks()) == []
        assert list(foo3.sources()) == []
        assert foo3.is_detached()
        assert foo3.creator() == foo2
        assert foo3.creator_detached() == (foo2, True)
        assert list(foo3.products()) == []
        # foo4
        assert list(foo4.sinks()) == []
        assert list(foo4.sources()) == []
        assert foo4.creator() == foo0
        assert foo4.creator_detached() == (foo0, False)
        assert list(foo4.products()) == []

        # Detach, recycle, clean, and check log messages
        foo0.detach()
        foo2.recycle(foo1)
        assert lt.format_str() == CHAIN2_FORMAT_STR
        lt.clean()
        rows = lt.db.execute("SELECT msg FROM log").fetchall()
        msgs = [row[0] for row in rows]
        assert msgs == [
            "init f:zero",
            "init f:one",
            "init f:two",
            "init f:three",
            "init f:four",
            "clean f:four",
        ]


CLEAN_SINK1_FORMAT_STR = """\
root:
             product   f:3

(f:0)
               value = 0
             product   (f:1)
             product   (f:2)

(f:1)
               value = 1
             creator   (f:0)
                sink   f:3

(f:2)
               value = 2
             creator   (f:0)
                sink   f:3

f:3
               value = 3
             creator   root:
              source   (f:1)
              source   (f:2)
"""


async def test_clean_sinks(lt):
    """Detached nodes only get removed when they have no sinks."""
    async with lt.db:
        foo0 = lt.create(Foo, lt.root, "0", value=0)
        foo1 = lt.create(Foo, foo0, "1", value=1)
        foo2 = lt.create(Foo, foo0, "2", value=2)
        foo3 = lt.create(Foo, lt.root, "3", value=3)
        foo3.add_source(foo1)
        foo3.add_source(foo2)
        foo0.detach()
        assert lt.format_str() == CLEAN_SINK1_FORMAT_STR
        lt.clean()
        assert list(lt.nodes(Foo, include_detached=True)) == [foo0, foo1, foo2, foo3]
        assert lt.format_str() == CLEAN_SINK1_FORMAT_STR
        foo3.detach()
        lt.clean()
        assert len(list(lt.nodes(Foo, include_detached=True))) == 0
        assert lt.format_str() == FROM_SCRATCH_FORMAT_STR


async def test_recycle_sinks(lt):
    """When a detached node with sinks is recycled, the sink relations remain."""
    async with lt.db:
        foo0 = lt.create(Foo, lt.root, "0", value=0)
        foo1 = lt.create(Foo, lt.root, "1", value=1)
        foo2 = lt.create(Foo, lt.root, "2", value=2)
        foo1.add_source(foo0)
        foo2.add_source(foo0)
        assert list(foo0.sinks()) == [foo1, foo2]
        foo0.detach()
        assert list(foo0.sinks()) == [foo1, foo2]
        lt.create(Foo, lt.root, "0")
        assert list(foo0.sinks()) == [foo1, foo2]


async def test_clean_nested(lt):
    async with lt.db:
        foo0 = lt.create(Foo, None, "0", value=0)
        foo1 = lt.create(Foo, None, "1", value=1)
        foo2 = lt.create(Foo, None, "2", value=2)
        foo3 = lt.create(Foo, lt.root, "3", value=3)

        # Test is_alive method
        assert foo0.is_alive()
        assert foo1.is_alive()
        assert foo2.is_alive()
        assert foo3.is_alive()

        foo1.add_source(foo0)
        foo2.add_source(foo1)
        foo3.add_source(foo2)
        lt.clean()
        assert lt.find(Foo, "3") == foo3
        assert lt.find_detached(Foo, "3") == (foo3, False)
        assert lt.find(Foo, "0") == foo0
        assert lt.find_detached(Foo, "0") == (foo0, True)
        foo3.detach()
        assert lt.find(Foo, "3") == foo3
        assert lt.find_detached(Foo, "3") == (foo3, True)
        assert lt.find(Foo, "0") == foo0
        assert lt.find_detached(Foo, "0") == (foo0, True)
        lt.clean()
        assert lt.find(Foo, "3") is None
        assert lt.find_detached(Foo, "3") == (None, None)
        assert lt.find(Foo, "0") is None
        assert lt.find_detached(Foo, "0") == (None, None)

        # Test is_alive method
        assert not foo0.is_alive()
        assert not foo1.is_alive()
        assert not foo2.is_alive()
        assert not foo3.is_alive()


async def test_create_detached(lt):
    async with lt.db:
        foo1 = lt.create(Foo, None, "some", value=0)
        assert foo1.is_detached()
        assert foo1.get_value() == 0
        foo2 = lt.create(Foo, None, "some", value=10)
        assert foo1 == foo2
        assert foo1.is_detached()
        assert foo2.is_detached()
        assert foo1.get_value() == 10
        assert foo2.get_value() == 10


async def test_create_detached_with_products_a(lt):
    async with lt.db:
        foo0 = lt.create(Foo, None, "0", value=0)
        foo1 = lt.create(Foo, foo0, "1", value=1)
        assert foo0.is_detached()
        assert foo1.is_detached()
        foo0_bis = lt.create(Foo, None, "0", value=0)
        assert foo0 == foo0_bis
        assert foo0.is_detached()
        assert foo0_bis.is_detached()
        assert foo1.creator() is None


async def test_create_detached_with_products_b(lt):
    async with lt.db:
        foo0 = lt.create(Foo, None, "0", value=0)
        foo1 = lt.create(Foo, foo0, "1", value=1)
        assert foo0.is_detached()
        assert foo1.is_detached()
        foo0_bis = lt.create(Foo, lt.root, "0", value=0)
        assert foo0 == foo0_bis
        assert not foo0.is_detached()
        assert not foo0_bis.is_detached()
        assert foo1.creator() is None


async def test_create_detached_try_cyclic(lt):
    async with lt.db:
        foo0 = lt.create(Foo, None, "0", value=0)
        foo1 = lt.create(Foo, foo0, "1", value=1)
        assert foo0.is_detached()
        assert foo1.is_detached()
        foo0_bis = lt.create(Foo, foo1, "0", value=0)
        assert foo0.is_detached()
        assert foo1.is_detached()
        assert foo0_bis.is_detached()
        assert foo1.creator() is None


async def test_duplicate_dependency(lt):
    async with lt.db:
        foo0 = lt.create(Foo, lt.root, "0", value=0)
        foo1 = lt.create(Foo, foo0, "1", value=1)
        foo0.add_source(foo1)
        with pytest.raises(GraphError):
            foo0.add_source(foo1)


async def test_cyclic1(lt):
    async with lt.db:
        foo0 = lt.create(Foo, lt.root, "0", value=0)
        foo1 = lt.create(Foo, foo0, "1", value=1)
        foo0.add_source(foo1)
        assert list(foo0.sources()) == [foo1]


async def test_cyclic2(lt):
    async with lt.db:
        foo0 = lt.create(Foo, lt.root, "0", value=0)
        foo1 = lt.create(Foo, lt.root, "1", value=1)
        foo1.add_source(foo0)
        with pytest.raises(CyclicError):
            foo0.add_source(foo1)


async def test_cyclic3(lt):
    async with lt.db:
        foo0 = lt.create(Foo, lt.root, "0", value=0)
        foo1 = lt.create(Foo, lt.root, "1", value=1)
        foo2 = lt.create(Foo, lt.root, "2", value=2)
        foo1.add_source(foo0)
        foo2.add_source(foo1)
        with pytest.raises(CyclicError):
            foo0.add_source(foo2)


async def test_check_no_cycle_batch(lt):
    async with lt.db:
        foo0 = lt.create(Foo, lt.root, "0", value=0)
        foo1 = lt.create(Foo, lt.root, "1", value=1)
        foo2 = lt.create(Foo, lt.root, "2", value=2)
        foo3 = lt.create(Foo, lt.root, "3", value=3)
        foo1.add_source(foo0)  # foo0 -> foo1
        foo2.add_source(foo1)  # foo1 -> foo2 (foo0's indirect sinks: {foo0, foo1, foo2})
        # foo3 is unrelated: not a sink of foo0.
        with pytest.raises(CyclicError):
            # foo2 in the batch would close a cycle; the whole batch must be rejected
            # before any edge is inserted.
            foo0.check_no_cycle_batch([foo3.i, foo2.i])
        assert list(foo0.sources()) == []
        # An all-acyclic batch does not raise.
        foo0.check_no_cycle_batch([foo3.i])


async def test_add_source_skip_cycle_check(lt):
    async with lt.db:
        foo0 = lt.create(Foo, lt.root, "0", value=0)
        foo1 = lt.create(Foo, lt.root, "1", value=1)
        foo1.add_source(foo0)  # foo0 -> foo1
        with pytest.raises(CyclicError):
            foo0.add_source(foo1)  # would close a cycle
        # skip_cycle_check=True bypasses the check, proving the flag is wired through.
        foo0.add_source(foo1, skip_cycle_check=True)
        assert list(foo0.sources()) == [foo1]


async def test_load_existing(path_tmp):
    with DBSession.open(path_tmp / "lt.db") as dblock1:
        lt = LogTrellis(dblock1)
        await lt.initialize()
        async with dblock1:
            graph = lt.format_str()

    with DBSession.open(path_tmp / "lt.db") as dblock2:
        lt = LogTrellis(dblock2)
        await lt.initialize()
        async with dblock2:
            assert lt.format_str() == graph


async def test_relocate_tree(lt):
    async with lt.db:
        # Create a LogCasecade with the following topology:
        # foo0 +-> foo1 +-> foo2
        #      |        +-> foo3
        #      +-> foo4
        foo0 = lt.create(Foo, lt.root, "0", value=0)
        foo1 = lt.create(Foo, foo0, "1", value=1)
        foo2 = lt.create(Foo, foo1, "2", value=2)
        lt.create(Foo, foo1, "3", value=3)
        foo4 = lt.create(Foo, foo0, "4", value=4)
        foo2.add_source(foo1)
        foo2.add_source(foo4)

        # Detach the foo1 node and attach it to the foo4 node.
        # The new topology is:
        # foo0 --> foo4 --> foo1 +-> foo2
        #                        +-> foo3
        foo1.detach()
        foo1.recycle(foo4)
        assert lt.format_str() == RELOCATE_FORMAT_STR


RELOCATE_FORMAT_STR = """\
root:
             product   f:0

f:0
               value = 0
             creator   root:
             product   f:4

f:1
               value = 1
             creator   f:4
             product   f:2
             product   f:3
                sink   f:2

f:2
               value = 2
             creator   f:1
              source   f:1
              source   f:4

f:3
               value = 3
             creator   f:1

f:4
               value = 4
             creator   f:0
             product   f:1
                sink   f:2
"""


async def test_check_consistency_creator(lt):
    async with lt.db:
        # Manually set creator field of foo0 node to None, without also detaching it.
        # This is now rejected by a CHECK constraint, instead of being caught later
        # by _check_consistency().
        foo0 = lt.create(Foo, lt.root, "0", value=0)
        with pytest.raises(sqlite3.IntegrityError):
            lt.db.execute("UPDATE node SET creator = NULL WHERE i = ?", (foo0.i,))


async def test_check_consistency_detached(lt):
    async with lt.db:
        # Manually detach foo0 while it has a creator.
        foo0 = lt.create(Foo, lt.root, "0", value=0)
        lt.db.execute("UPDATE node SET detached = TRUE WHERE i = ?", (foo0.i,))
        with pytest.raises(GraphError):
            lt._check_consistency()


async def test_check_consistency_second_root(lt):
    async with lt.db:
        # Manually make foo0 its own creator.
        # This is now rejected by a CHECK constraint, instead of being caught later
        # by _check_consistency().
        foo0 = lt.create(Foo, lt.root, "0", value=0)
        with pytest.raises(sqlite3.IntegrityError):
            lt.db.execute("UPDATE node SET creator = ? WHERE i = ?", (foo0.i, foo0.i))


RELOCATE_NESTED_FORMAT_STR = """\
root:
             product   f:0

f:0
               value = 0
             creator   root:
             product   f:4

(f:1)
               value = 1
             product   (f:3)
                sink   f:2

f:2
               value = 2
             creator   f:4
              source   (f:1)
              source   f:4

(f:3)
               value = 3
             creator   (f:1)

f:4
               value = 4
             creator   f:0
             product   f:2
                sink   f:2
"""


async def test_relocate_nested_detached(lt):
    async with lt.db:
        # Create a LogCasecade with the following topology:
        # foo0 +-> foo1 +-> foo2
        #      |        +-> foo3
        #      +-> foo4
        foo0 = lt.create(Foo, lt.root, "0", value=0)
        foo1 = lt.create(Foo, foo0, "1", value=1)
        foo2 = lt.create(Foo, foo1, "2", value=2)
        lt.create(Foo, foo1, "3", value=3)
        foo4 = lt.create(Foo, foo0, "4", value=4)
        foo2.add_source(foo1)
        foo2.add_source(foo4)

        # Detach the foo1 node and attach foo2 to foo4.
        # The new topology is:
        # foo0 --> foo4 --> foo2
        foo1.detach()
        foo2.recycle(foo4)
        print(lt.format_str())
        assert lt.format_str() == RELOCATE_NESTED_FORMAT_STR

        lt.clean()
        rows = lt.db.execute("SELECT msg FROM log").fetchall()
        msgs = [row[0] for row in rows]
        assert msgs == [
            "init f:0",
            "init f:1",
            "init f:2",
            "init f:3",
            "init f:4",
            "give_up f:1",
            "clean f:3",
        ]


async def test_trellis_recycle(lt):
    async with lt.db:
        # Create a LogTrellis with the following topology:
        # root +-> foo0 --> foo1 --> foo2
        #      +-> foo3 (source of foo1)
        foo0 = lt.create(Foo, lt.root, "0", value=0)
        foo1 = lt.create(Foo, foo0, "1", value=1)
        foo2 = lt.create(Foo, foo1, "2", value=2)
        foo3 = lt.create(Foo, lt.root, "3", value=3)
        foo1.add_source(foo3)

        # No detached node with this label.
        assert lt.recycle(Foo, lt.root, "9", value=9) is None
        # The node exists but is not detached.
        assert lt.recycle(Foo, lt.root, "1", value=1) is None

        foo1.detach()
        assert foo1.is_detached()
        assert foo2.is_detached()

        # The node is detached but the arguments are incompatible: can_recycle refuses.
        assert lt.recycle(Foo, lt.root, "1", value=42) is None
        assert foo1.is_detached()

        # Compatible arguments: the node is fully recycled with a new creator,
        # keeping its sources, products and satellite data.
        assert lt.recycle(Foo, lt.root, "1", value=1) == foo1
        assert not foo1.is_detached()
        assert not foo2.is_detached()
        assert foo1.creator() == lt.root
        assert list(foo1.sources()) == [foo3]
        assert list(foo1.products()) == [foo2]
        assert foo1.get_value() == 1

        # The update_recycled hook was invoked exactly once.
        rows = lt.db.execute("SELECT msg FROM log WHERE msg LIKE 'update_recycled %'").fetchall()
        assert rows == [("update_recycled f:1",)]
