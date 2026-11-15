# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.utils."""

import os
import subprocess
import sys

import pytest

from stepup.core.platform.popen import _resolve_interpreter, popen_args
from stepup.core.utils import (
    escape_control_chars,
    extract_env_overrides,
    format_subprocess,
    merge_resources,
    parse_resources,
    truncate_output,
)


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
    ("base", "override", "expected"),
    [
        # Basic merge: override adds a new key
        ("cpu:4", "gpu:1", "cpu:4,gpu:1"),
        # Override replaces an existing key
        ("cpu:4,gpu:1", "cpu:8", "cpu:8,gpu:1"),
        # Empty base: result is just the override
        ("", "cpu:4", "cpu:4"),
        # Empty override: result is just the base
        ("cpu:4", "", "cpu:4"),
        # Both empty: result is empty string
        ("", "", ""),
        # Override with multiple keys, some new and some replacing
        ("cpu:4,gpu:1,memgb:16", "gpu:2,memgb:32", "cpu:4,gpu:2,memgb:32"),
        # Value defaults to 1 when omitted in override
        ("cpu:4", "gpu", "cpu:4,gpu:1"),
        # Value defaults to 1 when omitted in base
        ("gpu", "cpu:4", "gpu:1,cpu:4"),
        # Override with zero value is valid
        ("cpu:4,gpu:1", "gpu:0", "cpu:4,gpu:0"),
        # Whitespace is stripped
        ("cpu : 4", " gpu : 1 ", "cpu:4,gpu:1"),
        # None base: result is just the override
        (None, "gpu:1", "gpu:1"),
        # None override: result is just the base
        ("cpu:4", None, "cpu:4"),
        # Both None: result is empty string
        (None, None, ""),
    ],
)
def test_merge_resources(base: str | None, override: str | None, expected: str) -> None:
    assert merge_resources(base, override) == expected


@pytest.mark.parametrize(
    "command",
    [
        "echo hello",
        'echo "Monday frown\nCoffee smile" > story.txt',
        pytest.param(
            "printf 'a\tb\r\n'",
            marks=pytest.mark.skipif(
                sys.platform == "win32",
                reason="The bash of Git for Windows drops carriage returns from a script.",
            ),
        ),
        "a'b'c=1; echo done",
        "echo '\x01\x02'",
    ],
)
def test_escape_control_chars_roundtrip(command, path_tmp):
    escaped = escape_control_chars(command)
    assert "\n" not in escaped
    # The `$'...'` quoting is a feature of bash, which the shell of a step need not be.
    bash = _resolve_interpreter("bash", os.environ, path_tmp)
    original, reproduced = (
        subprocess.run(
            popen_args([bash, "-c", script], shell=False, env=os.environ, cwd=path_tmp),
            capture_output=True,
            check=False,
            cwd=path_tmp,
        )
        for script in (command, escaped)
    )
    assert reproduced.stdout == original.stdout
    assert reproduced.returncode == original.returncode


def test_escape_control_chars_no_control_chars():
    command = 'echo "hello" > out.txt'
    assert escape_control_chars(command) == command


def test_format_subprocess_escapes_embedded_newline():
    line = format_subprocess("echo a\nb", ".", returncode=0, shell=True)
    assert "\n" not in line


@pytest.mark.parametrize(
    ("command", "env_overrides", "remaining"),
    [
        # No assignments.
        ("./script.py arg", None, "./script.py arg"),
        ("echo hello", None, "echo hello"),
        ("", None, ""),
        # A single assignment.
        ("FOO=bar ./script.py", {"FOO": "bar"}, "./script.py"),
        # Multiple assignments.
        ("A=1 B=2 ./run.sh", {"A": "1", "B": "2"}, "./run.sh"),
        # Quoted value with spaces.
        ('GREETING="hello world" ./show.py', {"GREETING": "hello world"}, "./show.py"),
        ("X='a b' ./show.py", {"X": "a b"}, "./show.py"),
        # Value containing an equals sign.
        ("KEY=a=b ./run.sh", {"KEY": "a=b"}, "./run.sh"),
        # Empty value.
        ("EMPTY= ./run.sh", {"EMPTY": ""}, "./run.sh"),
        # A non-leading assignment is not extracted.
        ("./cmd FOO=bar", None, "./cmd FOO=bar"),
        # The remaining placeholders are preserved verbatim.
        ("FOO=bar ./script.py ${inp} ${out}", {"FOO": "bar"}, "./script.py ${inp} ${out}"),
        # Lowercase command word that is not an assignment.
        ("9NOTVAR=1 ./run.sh", None, "9NOTVAR=1 ./run.sh"),
    ],
)
def test_extract_env_overrides(command, env_overrides, remaining):
    assert extract_env_overrides(command) == (env_overrides, remaining)


def test_truncate_output_unlimited():
    """A non-positive max_bytes returns the content unchanged, even when large."""
    content = "x" * 10_000
    assert truncate_output(content, 0) is content
    assert truncate_output(content, -1) is content


def test_truncate_output_env_default(monkeypatch):
    """Without an explicit max_bytes, the budget is taken from STEPUP_MAX_OUTPUT_SIZE."""
    content = "abcdefghij"
    monkeypatch.delenv("STEPUP_MAX_OUTPUT_SIZE", raising=False)
    assert truncate_output(content) is content
    monkeypatch.setenv("STEPUP_MAX_OUTPUT_SIZE", "5")
    assert truncate_output(content) == "abcde\n[output truncated at 5 bytes]\n"
    # An explicit budget still wins over the environment variable.
    assert truncate_output(content, 0) is content


def test_truncate_output_under_limit():
    """Content within the byte budget is returned unchanged."""
    content = "hello\n"
    assert truncate_output(content, 100) == content


def test_truncate_output_over_limit():
    """Content over the budget is cut and a sentinel line is appended."""
    content = "abcdefghij"  # 10 ASCII bytes
    result = truncate_output(content, 5)
    assert result == "abcde\n[output truncated at 5 bytes]\n"
    # The kept portion stays within the byte budget.
    assert len(result.split("\n")[0].encode("utf-8")) <= 5


def test_truncate_output_multibyte_boundary():
    """Cutting in the middle of a multi-byte character yields valid UTF-8 within budget."""
    content = "é" * 10  # each 'é' is 2 UTF-8 bytes => 20 bytes total
    # max_bytes 5 lands in the middle of the third 'é' (after 2 full chars = 4 bytes).
    result = truncate_output(content, 5)
    kept = result.split("\n")[0]
    assert kept == "éé"
    # The result is valid text (no replacement characters) and within the budget.
    assert "�" not in result
    assert len(kept.encode("utf-8")) <= 5
    assert result.endswith("[output truncated at 5 bytes]\n")
