# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.cattrs"""

import json

from path import Path

from stepup.core.cattrs import json_converter
from stepup.core.nglob import NGlobMulti


def test_path_round_trip():
    path = Path("./sub/file.txt")
    data = json_converter.unstructure(path)
    assert data == "./sub/file.txt"
    back = json_converter.structure(data, Path)
    assert back == path
    assert isinstance(back, Path)


def test_nglob_multi_round_trip_results():
    ngm = NGlobMulti.from_patterns(["inp*.txt"])
    ngm.extend(["inp1.txt", "inp2.txt"])
    data = json.loads(json.dumps(json_converter.unstructure(ngm)))
    back = json_converter.structure(data, NGlobMulti)
    assert back.equals(ngm)
    assert back.results == {(): [{"inp1.txt", "inp2.txt"}]}


def test_nglob_multi_round_trip_results_shared():
    # `NGlobMulti.results` sets must be the same objects as the corresponding
    # `NGlobSingle.results` sets, so that in-place updates from `NGlobSingle.extend` /
    # `.reduce` (used by `NGlobMulti.extend` / `.reduce` and, transitively, `.will_change`)
    # are visible through `NGlobMulti.results` without a separate write.
    # A cattrs structure hook that rebuilds `NGlobMulti.results` from independently
    # deserialized data (instead of reusing the `NGlobSingle.results` objects)
    # breaks this invariant.
    ngm = NGlobMulti.from_patterns(["inp*.txt"])
    ngm.extend(["inp1.txt", "inp2.txt"])
    data = json.loads(json.dumps(json_converter.unstructure(ngm)))
    back = json_converter.structure(data, NGlobMulti)
    for values, path_sets in back.results.items():
        for ngs, path_set in zip(back.nglob_singles, path_sets, strict=True):
            assert path_set is ngs.results[values]


def test_nglob_multi_round_trip_will_change():
    ngm = NGlobMulti.from_patterns(["inp*.txt"])
    ngm.extend(["inp1.txt", "inp2.txt"])
    data = json.loads(json.dumps(json_converter.unstructure(ngm)))
    back = json_converter.structure(data, NGlobMulti)
    evolved = back.will_change({"inp1.txt"}, {"inp3.txt"})
    assert evolved is not None
    assert evolved.results == {(): [{"inp2.txt", "inp3.txt"}]}


def test_nglob_multi_round_trip_named():
    ngm = NGlobMulti.from_patterns(["${*dir}/foo.txt", "${*dir}/bar${*id}.csv"])
    ngm.extend(["a/foo.txt", "a/bar1.csv", "b/foo.txt", "b/bar2.csv"])
    data = json.loads(json.dumps(json_converter.unstructure(ngm)))
    back = json_converter.structure(data, NGlobMulti)
    assert back.equals(ngm)
    assert back.used_names == ("dir", "id")
    assert back.results == {
        ("a", "1"): [{"a/foo.txt"}, {"a/bar1.csv"}],
        ("b", "2"): [{"b/foo.txt"}, {"b/bar2.csv"}],
    }
    evolved = back.will_change(set(), {"a/bar3.csv"})
    assert evolved is not None
    assert evolved.results[("a", "3")] == [{"a/foo.txt"}, {"a/bar3.csv"}]
