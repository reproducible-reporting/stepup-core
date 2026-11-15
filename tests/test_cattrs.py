# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.cattrs"""

import enum
import json

import path as path_lib
import pytest
import yaml

from stepup.core.cattrs import get_yaml_converter, json_converter
from stepup.core.nglob import NamedGlob
from stepup.core.path import Path

CONVERTERS = [
    pytest.param(json_converter, json.dumps, json.loads, id="json"),
    pytest.param(get_yaml_converter(), yaml.safe_dump, yaml.safe_load, id="yaml"),
]


# A `(str, Enum)` mixin, defined with the functional API to keep ruff from suggesting `StrEnum`,
# whose `str()` is its value, unlike that of the mixin.
MixinColor = enum.Enum("MixinColor", {"RED": "red"}, type=str)


class StrColor(enum.StrEnum):
    RED = "red"


@pytest.mark.parametrize(("converter", "dumps", "loads"), CONVERTERS)
def test_path_round_trip(converter, dumps, loads):
    path = Path("./sub/file.txt")
    data = converter.unstructure(path)
    assert data == "./sub/file.txt"
    assert type(data) is str
    back = converter.structure(loads(dumps(data)), Path)
    assert back == path
    assert type(back) is Path


@pytest.mark.parametrize(("converter", "dumps", "loads"), CONVERTERS)
def test_upstream_path_round_trip(converter, dumps, loads):
    """A field annotated with `Path` of the `path` library is structured as that class."""
    path = path_lib.Path("./sub/file.txt")
    data = converter.unstructure(path)
    assert data == "./sub/file.txt"
    assert type(data) is str
    back = converter.structure(loads(dumps(data)), path_lib.Path)
    assert back == path
    assert type(back) is path_lib.Path


@pytest.mark.parametrize("cls", [MixinColor, StrColor])
@pytest.mark.parametrize(("converter", "dumps", "loads"), CONVERTERS)
def test_string_enum_round_trip(converter, dumps, loads, cls):
    """A string enum is written as its value, not as its name."""
    data = loads(dumps(converter.unstructure({"color": cls.RED})))
    assert data == {"color": "red"}
    assert converter.structure(data["color"], cls) is cls.RED


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
