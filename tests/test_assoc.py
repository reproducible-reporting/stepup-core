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
"""Unit tests for stepup.core.assoc."""

import pytest

from stepup.core.assoc import Assoc

MAX_CASES = [(None, None), (1, 2), (2, 1), (2, None), (None, 2), (1, None), (None, 1)]
MAX_CASES_SRC_MULTI = [case for case in MAX_CASES if case[1] != 1]
MAX_CASES_DST_MULTI = [case for case in MAX_CASES if case[0] != 1]
MAX_CASES_MULTI = [case for case in MAX_CASES if case[0] != 1 and case[1] != 1]


@pytest.mark.parametrize("dst_max, src_max", MAX_CASES)
def test_empty(dst_max, src_max):
    a = Assoc(dst_max, src_max)
    if dst_max is None:
        assert a._max is None
    else:
        assert a._max == dst_max
    if src_max is None:
        assert a.inverse._max is None
    else:
        assert a.inverse._max == src_max
    assert list(a.items()) == []
    assert list(a.pairs()) == []
    assert list(a.keys()) == []
    assert len(a) == 0
    assert list(a.inverse.items()) == []
    assert list(a.inverse.pairs()) == []
    assert list(a.inverse.keys()) == []
    assert len(a.inverse) == 0


@pytest.mark.parametrize("dst_max, src_max", MAX_CASES)
@pytest.mark.parametrize("discard", ["pair", "all", "inverse_all"])
def test_modify_1(dst_max, src_max, discard):
    a = Assoc(dst_max, src_max)
    a.add(1, "a")
    a[2] = "b"
    with pytest.raises(KeyError):
        _ = a[3]
    assert a.get(3) is None
    assert a.get(3, ["aaa"]) == ["aaa"]
    if dst_max == 1:
        assert a[1] == "a"
        assert a[2] == "b"
        assert a.get(1) == "a"
        assert a.get(2) == "b"
        assert list(a.items()) == [(1, "a"), (2, "b")]
    else:
        assert a[1] == {"a"}
        assert a[2] == {"b"}
        assert a.get(1) == {"a"}
        assert a.get(2) == {"b"}
        assert list(a.items()) == [(1, {"a"}), (2, {"b"})]
    assert list(a.pairs()) == [(1, "a"), (2, "b")]
    assert list(a.keys()) == [1, 2]
    assert len(a) == 2

    with pytest.raises(KeyError):
        _ = a.inverse["c"]
    assert a.inverse.get("c") is None
    if src_max == 1:
        assert a.inverse["a"] == 1
        assert a.inverse["b"] == 2
        assert a.inverse.get("a") == 1
        assert a.inverse.get("b") == 2
        assert list(a.inverse.items()) == [("a", 1), ("b", 2)]
    else:
        assert a.inverse["a"] == {1}
        assert a.inverse["b"] == {2}
        assert a.inverse.get("a") == {1}
        assert a.inverse.get("b") == {2}
        assert list(a.inverse.items()) == [("a", {1}), ("b", {2})]
    assert list(a.inverse.pairs()) == [("a", 1), ("b", 2)]
    assert list(a.inverse.keys()) == ["a", "b"]
    assert len(a.inverse) == 2
    if discard == "pair":
        a.discard(1, "a")
    elif discard == "all":
        a.discard(1)
    elif discard == "inverse_all":
        a.inverse.discard("a")
    else:
        raise NotImplementedError
    if dst_max == 1:
        assert list(a.items()) == [(2, "b")]
    else:
        assert list(a.items()) == [(2, {"b"})]
    assert list(a.keys()) == [2]
    assert len(a) == 1
    if src_max == 1:
        assert list(a.inverse.items()) == [("b", 2)]
    else:
        assert list(a.inverse.items()) == [("b", {2})]
    assert list(a.inverse.keys()) == ["b"]
    assert len(a.inverse) == 1


@pytest.mark.parametrize("dst_max, src_max", MAX_CASES_DST_MULTI)
def test_modify_2_dst(dst_max, src_max):
    a = Assoc(dst_max, src_max)
    a.add(1, "a")
    a.add(2, "b")
    a.add(1, "a+")
    assert 1 in a
    assert 3 not in a
    assert "a" in a.inverse
    assert "c" not in a.inverse
    assert a.has(1, "a")
    assert not a.has(2, "a")
    assert not a.has(1, "b")
    assert a.inverse.has("a", 1)
    assert not a.inverse.has("a", 2)
    assert not a.inverse.has("b", 1)
    assert list(a.items()) == [(1, {"a", "a+"}), (2, {"b"})]
    assert list(a.pairs()) == [(1, "a"), (1, "a+"), (2, "b")]
    assert list(a.keys()) == [1, 2]
    assert len(a) == 2
    if src_max == 1:
        assert list(a.inverse.items()) == [("a", 1), ("b", 2), ("a+", 1)]
        with pytest.raises(ValueError):
            a.add(3, "a")
        assert (3, "a") not in a
    else:
        assert list(a.inverse.items()) == [("a", {1}), ("b", {2}), ("a+", {1})]
    assert list(a.inverse.pairs()) == [("a", 1), ("b", 2), ("a+", 1)]
    assert list(a.inverse.keys()) == ["a", "b", "a+"]
    assert len(a.inverse) == 3
    a[1] = ["c", "d"]
    assert list(a.items()) == [(2, {"b"}), (1, {"c", "d"})]
    if src_max == 1:
        assert list(a.inverse.items()) == [("b", 2), ("c", 1), ("d", 1)]
    else:
        assert list(a.inverse.items()) == [("b", {2}), ("c", {1}), ("d", {1})]
    if dst_max == 2:
        with pytest.raises(ValueError):
            a[1] = ["c", "d", "e"]
        assert list(a.items()) == [(2, {"b"}), (1, {"c", "d"})]
        if src_max == 1:
            assert list(a.inverse.items()) == [("b", 2), ("c", 1), ("d", 1)]
        else:
            assert list(a.inverse.items()) == [("b", {2}), ("c", {1}), ("d", {1})]
    else:
        a[1] = ["c", "d", "e"]
        assert list(a.items()) == [(2, {"b"}), (1, {"c", "d", "e"})]
        if src_max == 1:
            assert list(a.inverse.items()) == [("b", 2), ("c", 1), ("d", 1), ("e", 1)]
        else:
            assert list(a.inverse.items()) == [
                ("b", {2}),
                ("c", {1}),
                ("d", {1}),
                ("e", {1}),
            ]


@pytest.mark.parametrize("dst_max, src_max", MAX_CASES_SRC_MULTI)
def test_modify_2_src(dst_max, src_max):
    a = Assoc(dst_max, src_max)
    a.add(1, "a")
    a.add(1, "a")
    a.add(2, "b")
    a.add(3, "a")
    if dst_max == 1:
        assert list(a.items()) == [(1, "a"), (2, "b"), (3, "a")]
        with pytest.raises(ValueError):
            a.add(1, "c")
    else:
        assert list(a.items()) == [(1, {"a"}), (2, {"b"}), (3, {"a"})]
    assert list(a.pairs()) == [(1, "a"), (2, "b"), (3, "a")]
    assert list(a.keys()) == [1, 2, 3]
    assert len(a) == 3
    assert list(a.inverse.items()) == [("a", {1, 3}), ("b", {2})]
    assert list(a.inverse.keys()) == ["a", "b"]
    assert len(a.inverse) == 2


@pytest.mark.parametrize("dst_max, src_max", MAX_CASES_MULTI)
def test_modify_2_dst_src(dst_max, src_max):
    a = Assoc(dst_max, src_max)
    a.add(1, "a")
    a.add(2, "b")
    a.add(3, "a")
    a.add(1, "c")
    a.add(1, "c")
    assert list(a.items()) == [(1, {"a", "c"}), (2, {"b"}), (3, {"a"})]
    assert list(a.pairs()) == [(1, "a"), (1, "c"), (2, "b"), (3, "a")]
    assert list(a.keys()) == [1, 2, 3]
    assert len(a) == 3
    assert list(a.inverse.items()) == [("a", {1, 3}), ("b", {2}), ("c", {1})]
    assert list(a.inverse.pairs()) == [("a", 1), ("a", 3), ("b", 2), ("c", 1)]
    assert list(a.inverse.keys()) == ["a", "b", "c"]
    assert len(a.inverse) == 3
    a.add(2, "d")
    a.add(4, "b")
    a.discard(1, "a", insist=True)
    a.discard(1, "a")
    with pytest.raises(KeyError):
        a.discard(1, "a", insist=True)
    a.discard(3, "a")
    assert list(a.items()) == [(1, {"c"}), (2, {"b", "d"}), (4, {"b"})]
    assert list(a.keys()) == [1, 2, 4]
    assert len(a) == 3
    assert list(a.inverse.items()) == [("b", {2, 4}), ("c", {1}), ("d", {2})]
    assert list(a.inverse.keys()) == ["b", "c", "d"]
    assert len(a.inverse) == 3
    a.discard("a")  # no effect
    assert list(a.items()) == [(1, {"c"}), (2, {"b", "d"}), (4, {"b"})]
    assert list(a.keys()) == [1, 2, 4]
    assert len(a) == 3
    assert list(a.inverse.items()) == [("b", {2, 4}), ("c", {1}), ("d", {2})]
    assert list(a.inverse.keys()) == ["b", "c", "d"]
    assert len(a.inverse) == 3
    a.discard(4)
    with pytest.raises(KeyError):
        a.discard(4, insist=True)
    a.discard(4)
    assert list(a.items()) == [(1, {"c"}), (2, {"b", "d"})]
    assert list(a.keys()) == [1, 2]
    assert len(a) == 2
    assert list(a.inverse.items()) == [("b", {2}), ("c", {1}), ("d", {2})]
    assert list(a.inverse.keys()) == ["b", "c", "d"]
    assert len(a.inverse) == 3
    a.inverse.discard(2)  # no effect
    assert list(a.items()) == [(1, {"c"}), (2, {"b", "d"})]
    assert list(a.keys()) == [1, 2]
    assert len(a) == 2
    assert list(a.inverse.items()) == [("b", {2}), ("c", {1}), ("d", {2})]
    assert list(a.inverse.keys()) == ["b", "c", "d"]
    assert len(a.inverse) == 3
    a.inverse.discard("c")
    assert list(a.items()) == [(2, {"b", "d"})]
    assert list(a.keys()) == [2]
    assert len(a) == 1
    assert list(a.inverse.items()) == [("b", {2}), ("d", {2})]
    assert list(a.inverse.keys()) == ["b", "d"]
    assert len(a.inverse) == 2

    if a.inverse.max == 2:
        a.add(3, "b")
        a.add(3, "b")
        with pytest.raises(ValueError):
            a.add(4, "b")
    if a.max == 2:
        with pytest.raises(ValueError):
            a.add(2, "a")
