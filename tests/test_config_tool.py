# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.config_tool.

These drive the `stepup` command line end to end,
because what the config tool is for is how a configuration and its problems come out
on the terminal, which only the real subparsers of the real subcommands produce.
"""

import ast
import sys
import tomllib
from decimal import Decimal

import pytest
from path import Path

import stepup.core
from stepup.core.__main__ import _setup_cli, main
from stepup.core.config_tool import _toml_value
from stepup.core.constants import CORE_ENV_VARS, INTERNAL_ENV_VARS
from stepup.core.enums import ReturnCode
from stepup.core.exceptions import ConfigError


def _run_main(monkeypatch, path_tmp: Path, argv: list[str]) -> int:
    """Run the `stepup` command line in this process and return its exit code.

    A subcommand that completes without raising `SystemExit` exits with code 0.
    """
    monkeypatch.setenv("STEPUP_ROOT", path_tmp)
    monkeypatch.setattr(sys, "argv", ["stepup", *argv])
    try:
        main()
    except SystemExit as exc:
        return exc.code
    return 0


def test_cli_config_error_without_traceback(monkeypatch, path_tmp, capsys, clean_env):
    """A broken config file stops a build with a message instead of a traceback."""
    (path_tmp / "stepup.toml").write_bytes(b"jbos = 4\n")
    assert _run_main(monkeypatch, path_tmp, ["build"]) == ReturnCode.INTERNAL.value
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Traceback" not in captured.err
    assert (
        "unsupported key 'jbos' at the top level (did you mean 'jobs' in section [build]?)"
        in captured.err
    )


def test_cli_config_error_traceback_in_debug(monkeypatch, path_tmp, clean_env):
    """`STEPUP_DEBUG` keeps the traceback, as it does for any other usage error."""
    (path_tmp / "stepup.toml").write_bytes(b"jbos = 4\n")
    monkeypatch.setenv("STEPUP_DEBUG", "1")
    monkeypatch.setenv("STEPUP_ROOT", path_tmp)
    monkeypatch.setattr(sys, "argv", ["stepup", "build"])
    with pytest.raises(ConfigError, match="unsupported key 'jbos'"):
        main()


def test_cli_config_survives_config_error(monkeypatch, path_tmp, capsys, clean_env):
    """`stepup config` runs despite a broken config, because it explains what is broken."""
    (path_tmp / "stepup.toml").write_bytes(b"jbos = 4\n")
    assert _run_main(monkeypatch, path_tmp, ["config"]) == ReturnCode.INTERNAL.value
    captured = capsys.readouterr()
    assert "jbos = 4" in captured.out
    assert "<-- ERROR: unsupported key 'jbos'" in captured.out
    # Nothing is left to report separately when every problem is shown inline.
    assert captured.err == ""


def _find_line(out: str, prefix: str) -> str:
    """Return the one line of the `config` output that starts with the given prefix."""
    (line,) = [line for line in out.splitlines() if line.startswith(prefix)]
    return line


def test_cli_config_inlines_setting_problems(monkeypatch, path_tmp, capsys, clean_env):
    """A problem with a setting or a section is shown on the line it concerns."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'build = 5\nfoo = "bar"\n[buidl]\nx = 1\n')
    assert _run_main(monkeypatch, path_tmp, ["config"]) == ReturnCode.INTERNAL.value
    captured = capsys.readouterr()
    assert _find_line(captured.out, "build = 5").endswith(
        "<-- ERROR: 'build' is configured as a value, but a section [build] is expected"
    )
    assert _find_line(captured.out, 'foo = "bar"').endswith(
        "<-- ERROR: unsupported key 'foo' at the top level"
    )
    # A section header names no config file, so the problem shown there does.
    assert _find_line(captured.out, "[buidl]").endswith(
        f"# <-- ERROR: {cfg}: unknown section [buidl] (did you mean 'build'?)"
    )
    assert captured.err == ""
    # The problems are shown in comments, so the output remains valid TOML.
    assert tomllib.loads(captured.out) == {"build": 5, "foo": "bar", "buidl": {"x": 1}}


def test_cli_config_inlines_file_and_env_problems(monkeypatch, path_tmp, capsys, clean_env):
    """A problem with a whole file or with an environment variable is shown where it is listed."""
    (path_tmp / "stepup.toml").write_bytes(b"jobs = = 4\n")
    monkeypatch.setenv("STEPUP_BUILD_JOBS", "abc")
    assert _run_main(monkeypatch, path_tmp, ["config"]) == ReturnCode.INTERNAL.value
    captured = capsys.readouterr()
    assert "<-- ERROR: invalid TOML syntax: " in _find_line(captured.out, "#   FOUND:  ")
    assert "<-- ERROR: " in _find_line(captured.out, "#   STEPUP_BUILD_JOBS = ")
    assert captured.err == ""


def test_cli_config_renders_decimal_setting_as_string(monkeypatch, path_tmp, capsys, clean_env):
    """A `Decimal` setting renders as a string, the only form that keeps a scale factor apart.

    See `positive_decimal` in `tui.py`: `Decimal(3.0)` is `Decimal("3")`,
    so a TOML float would turn `3.0` times the number of cores into 3 jobs.
    """
    monkeypatch.setenv("STEPUP_BUILD_JOBS", "3.0")
    assert _run_main(monkeypatch, path_tmp, ["config"]) == 0
    out = capsys.readouterr().out
    assert _find_line(out, "jobs = ") == 'jobs = "3.0"  # $STEPUP_BUILD_JOBS'
    assert tomllib.loads(out)["build"]["jobs"] == "3.0"


def test_cli_config_groups_env_vars(monkeypatch, path_tmp, capsys, clean_env):
    """Each variable is listed in the group that says what it does, or fails to do."""
    monkeypatch.setenv("STEPUP_BUILD_JOBS", "3")
    monkeypatch.setenv("STEPUP_JOB_I", "0")
    monkeypatch.setenv("STEPUP_BUILD_JBOS", "3")
    assert _run_main(monkeypatch, path_tmp, ["config"]) == 0
    lines = capsys.readouterr().out.splitlines()
    setting = lines.index("# Configuration environment variables:")
    core = lines.index("# StepUp Core module environment variables:")
    internal = lines.index(
        "# Internal environment variables, overruled by StepUp (probably a mistake):"
    )
    unknown = lines.index("# Unrecognized environment variables, without effect:")
    assert setting < core < internal < unknown
    assert lines[setting + 1] == '#   STEPUP_BUILD_JOBS = "3"'
    assert lines[core + 1] == f'#   STEPUP_ROOT = "{path_tmp}"'
    assert lines[internal + 1] == '#   STEPUP_JOB_I = "0"'
    assert lines[unknown + 1] == '#   STEPUP_BUILD_JBOS = "3"'


def test_cli_config_without_any_configuration(monkeypatch, path_tmp, capsys, clean_env):
    """With nothing configured at all, the output says so instead of listing only the sources."""
    monkeypatch.chdir(path_tmp)
    monkeypatch.setattr(sys, "argv", ["stepup", "config"])
    main()
    lines = capsys.readouterr().out.rstrip("\n").splitlines()
    assert lines[-1] == "# No configuration found."
    assert lines[-2] == ""


def test_listed_env_vars_are_not_settings(monkeypatch, path_tmp, clean_env):
    """A variable listed by name would never be shown as such once it becomes a setting."""
    monkeypatch.setenv("STEPUP_ROOT", path_tmp)
    _, loader = _setup_cli()
    assert CORE_ENV_VARS.isdisjoint(loader.recognized_env_vars())
    assert INTERNAL_ENV_VARS.isdisjoint(loader.recognized_env_vars())


def _looks_like_env(node: ast.expr) -> bool:
    """Whether an expression is plausibly an environment dict, judged by its source text."""
    return "env" in ast.unparse(node).lower()


def _env_var_key(node: ast.AST) -> ast.expr | None:
    """Return the expression naming the environment variable that a node looks up, if any.

    Recognized are `getenv(...)` whatever its receiver,
    which covers `os.getenv` as well as the `getenv` of `stepup.core.api`,
    and `.get(...)` or a subscript on something whose source text mentions the environment.
    """
    if isinstance(node, ast.Subscript) and _looks_like_env(node.value):
        return node.slice
    if not (isinstance(node, ast.Call) and len(node.args) > 0):
        return None
    func = node.func
    if isinstance(func, ast.Name) and func.id == "getenv":
        return node.args[0]
    if isinstance(func, ast.Attribute) and (
        func.attr == "getenv" or (func.attr == "get" and _looks_like_env(func.value))
    ):
        return node.args[0]
    return None


def _env_var_names(source: str) -> set[str]:
    """Return every environment variable that a module looks up by literal name.

    A lookup with a computed name stays invisible here, which is intended:
    those are the settings, whose names the patched parsers derive.
    """
    names = set()
    for node in ast.walk(ast.parse(source)):
        key = _env_var_key(node)
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            names.add(key.value)
    return names


def test_env_vars_are_classified(monkeypatch, path_tmp, clean_env):
    """Every prefixed variable that StepUp Core looks up by name is accounted for.

    Without this, adding a variable to the code and forgetting `CORE_ENV_VARS`
    makes `stepup config` report it as being without effect, which is worse than
    not listing it at all.
    """
    monkeypatch.setenv("STEPUP_ROOT", path_tmp)
    _, loader = _setup_cli()
    found = set()
    for path in Path(stepup.core.__file__).parent.glob("*.py"):
        found.update(
            name for name in _env_var_names(path.read_text()) if name.startswith("STEPUP_")
        )
    listed = CORE_ENV_VARS | INTERNAL_ENV_VARS
    assert found - listed - loader.recognized_env_vars() == set()
    assert listed - found == set()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # `bool` is checked before `int`, because `isinstance(True, int)` is true.
        (True, "true"),
        (False, "false"),
        (7, "7"),
        (-1, "-1"),
        (1.5, "1.5"),
        ("plain", '"plain"'),
        ("", '""'),
        ([1, "a", True], '[1, "a", true]'),
        ([], "[]"),
        # A `Decimal` has no TOML literal, so it renders as the string form it round-trips as.
        (Decimal("3"), '"3"'),
        (Decimal("1.5"), '"1.5"'),
    ],
)
def test_toml_value(value, expected):
    assert _toml_value(value) == expected


class _Quoting:
    """A value of a type TOML has no literal for, whose string form needs escaping."""

    def __str__(self) -> str:
        return 'a"b\\c'


@pytest.mark.parametrize(
    "value",
    ['say "hi"', "back\\slash", "two\nlines", 'all\\of "them"\n', _Quoting()],
)
def test_toml_value_survives_a_round_trip(value):
    """Whatever needs escaping is escaped, including in the fallback for an unknown type."""
    assert tomllib.loads(f"x = {_toml_value(value)}") == {"x": str(value)}


def test_cli_config_reports_second_problem_on_a_line_apart(
    monkeypatch, path_tmp, capsys, clean_env
):
    """Two problems on one line would need two comments, so only the first is shown there."""
    (path_tmp / ".stepup.toml").write_bytes(b"[buidl]\njobs = 2\n")
    (path_tmp / "stepup.toml").write_bytes(b"[buidl]\njobs = 3\n")
    assert _run_main(monkeypatch, path_tmp, ["config"]) == ReturnCode.INTERNAL.value
    captured = capsys.readouterr()
    header = _find_line(captured.out, "[buidl]")
    assert header.count("<-- ERROR: ") == 1
    assert header.endswith(
        f"# <-- ERROR: {path_tmp / '.stepup.toml'}: unknown section [buidl] (did you mean 'build'?)"
    )
    assert "unknown section [buidl]" in captured.err
    assert str(path_tmp / "stepup.toml") in captured.err
    # The problems are shown in comments, so the output remains valid TOML.
    assert tomllib.loads(captured.out) == {"buidl": {"jobs": 3}}


def test_cli_config_reports_overridden_setting_apart(monkeypatch, path_tmp, capsys, clean_env):
    """A problem about a setting that another config file overrides has no line of its own."""
    low = path_tmp / ".stepup.toml"
    low.write_bytes(b'[build]\njobs = "abc"\n')
    (path_tmp / "stepup.toml").write_bytes(b"[build]\njobs = 4\n")
    assert _run_main(monkeypatch, path_tmp, ["config"]) == ReturnCode.INTERNAL.value
    captured = capsys.readouterr()
    assert "<-- ERROR" not in captured.out
    assert f"{low}: jobs in section [build]: " in captured.err
