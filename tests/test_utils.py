# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright 2024-2026 Toon Verstraelen
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
"""Tests for stepup.core.utils."""

import subprocess

import pytest

from stepup.core.utils import escape_command_display, format_subprocess, parse_resources


@pytest.mark.parametrize(
    ("s", "expected"),
    [
        ("cpu:4,gpu:1,memgb:16", {"cpu": 4, "gpu": 1, "memgb": 16}),
        ("cpu:2", {"cpu": 2}),
        ("cpu:0", {"cpu": 0}),
        ("cpu:", {"cpu": 1}),
        ("cpu", {"cpu": 1}),
        ("  cpu : 4 , gpu ", {"cpu": 4, "gpu": 1}),
        ("", {}),
        (",", {}),
        (",,,", {}),
    ],
)
def test_parse_resources(s, expected):
    assert parse_resources(s) == expected


@pytest.mark.parametrize(
    "s",
    [
        "cpu:-1",
        ":1",
        "  :2",
    ],
)
def test_parse_resources_invalid(s):
    with pytest.raises(ValueError):
        parse_resources(s)


@pytest.mark.parametrize(
    "command",
    [
        "echo hello",
        'echo "Monday frown\nCoffee smile" > story.txt',
        "printf 'a\tb\r\n'",
        "a'b'c=1; echo done",
        "echo '\x01\x02'",
    ],
)
def test_escape_command_display_roundtrip(command, path_tmp):
    escaped = escape_command_display(command)
    assert "\n" not in escaped
    original = subprocess.run(
        ["bash", "-c", command], capture_output=True, check=False, cwd=path_tmp
    )
    reproduced = subprocess.run(
        ["bash", "-c", escaped], capture_output=True, check=False, cwd=path_tmp
    )
    assert reproduced.stdout == original.stdout
    assert reproduced.returncode == original.returncode


def test_escape_command_display_no_control_chars():
    command = 'echo "hello" > out.txt'
    assert escape_command_display(command) == command


def test_format_subprocess_escapes_embedded_newline():
    line = format_subprocess("echo a\nb", ".", None, 0, shell=True)
    assert "\n" not in line
