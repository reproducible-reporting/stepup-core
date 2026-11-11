# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.cattrs"""

import json

import path as path_lib

from stepup.core.cattrs import json_converter
from stepup.core.nglob import NamedGlob
from stepup.core.path import Path


def test_path_round_trip():
    path = Path("./sub/file.txt")
    data = json_converter.unstructure(path)
    assert data == "./sub/file.txt"
    assert type(data) is str
    back = json_converter.structure(data, Path)
    assert back == path
    assert type(back) is Path


def test_upstream_path_round_trip():
    """A field annotated with `Path` of the `path` library is structured as that class."""
    path = path_lib.Path("./sub/file.txt")
    data = json_converter.unstructure(path)
    assert data == "./sub/file.txt"
    assert type(data) is str
    back = json_converter.structure(data, path_lib.Path)
    assert back == path
    assert type(back) is path_lib.Path


def test_named_glob_round_trip_results():
    ng = NamedGlob("inp*.txt")
    ng.extend(["inp1.txt", "inp2.txt"])
    data = json.loads(json.dumps(json_converter.unstructure(ng)))
    back = json_converter.structure(data, NamedGlob)
    assert back.results == ng.results
    assert back.results == {(): {"inp1.txt", "inp2.txt"}}


def test_named_glob_round_trip_will_change():
    ng = NamedGlob("inp*.txt")
    ng.extend(["inp1.txt", "inp2.txt"])
    data = json.loads(json.dumps(json_converter.unstructure(ng)))
    back = json_converter.structure(data, NamedGlob)
    evolved = back.will_change({"inp1.txt"}, {"inp3.txt"})
    assert evolved is not None
    assert evolved.results == {(): {"inp2.txt", "inp3.txt"}}
