# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.cli.config.

These drive the `stepup` command line end to end,
because what the config tool is for is how a configuration and its problems come out
on the terminal, which only the real subparsers of the real subcommands produce.
"""

import ast
import sys
import tomllib
from decimal import Decimal

import pytest
from core_common import add_env_var_entry_points

import stepup.core
from stepup.core.__main__ import _setup_cli, main
from stepup.core.cli.config import _toml_value
from stepup.core.cli.env_vars_loader import core_env_vars
from stepup.core.enums import ReturnCode
from stepup.core.env_vars import EnvVarInfo, EnvVarRegistry
from stepup.core.exceptions import ConfigError
from stepup.core.path import Path


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

    See `positive_decimal` in `utils.py`: `Decimal(3.0)` is `Decimal("3")`,
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
    core = lines.index("# StepUp Core environment variables:")
    internal = lines.index(
        "# Internal environment variables, overruled by StepUp (probably a mistake):"
    )
    unknown = lines.index("# Unrecognized environment variables, without effect:")
    assert setting < core < internal < unknown
    assert lines[setting + 1] == '#   STEPUP_BUILD_JOBS = "3"'
    assert lines[core + 1] == f'#   STEPUP_ROOT = "{path_tmp}"'
    assert lines[internal + 1] == '#   STEPUP_JOB_I = "0"'
    assert lines[unknown + 1] == (
        "#   STEPUP_BUILD_JBOS = <set, hidden> (did you mean 'STEPUP_BUILD_JOBS'?)"
    )


def test_cli_config_without_any_configuration(monkeypatch, path_tmp, capsys, clean_env):
    """With nothing configured at all, the output says so instead of listing only the sources."""
    monkeypatch.chdir(path_tmp)
    monkeypatch.setattr(sys, "argv", ["stepup", "config"])
    main()
    lines = capsys.readouterr().out.rstrip("\n").splitlines()
    assert lines[-1] == "# No configuration found."
    assert lines[-2] == ""


def test_registered_env_vars_are_not_settings(monkeypatch, path_tmp, clean_env):
    """A registered variable would never be shown as such once it becomes a setting."""
    monkeypatch.setenv("STEPUP_ROOT", path_tmp)
    _, loader = _setup_cli()
    assert core_env_vars().env_vars.keys().isdisjoint(loader.recognized_env_vars())


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
    """Every prefixed variable that StepUp Core looks up by name is registered, and vice versa.

    Without this, adding a variable to the code and forgetting `core_env_vars`
    makes `stepup config` report it as being without effect, which is worse than
    not listing it at all.
    """
    monkeypatch.setenv("STEPUP_ROOT", path_tmp)
    _, loader = _setup_cli()
    found = set()
    for path in Path(stepup.core.__file__).parent.walkfiles("*.py"):
        found.update(
            name
            for name in _env_var_names(path.read_text(encoding="utf-8"))
            if name.startswith("STEPUP_")
        )
    listed = set(core_env_vars().env_vars)
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


#
# Environment variables of extensions
#


def fake_env_vars() -> EnvVarRegistry:
    return EnvVarRegistry(
        "StepUp Fake",
        {
            "STEPUP_FAKE_TOKEN": EnvVarInfo(secret=True, validator=_parse_token),
            "STEPUP_FAKE_MODE": EnvVarInfo(secret=False, validator=_parse_mode),
            "STEPUP_FAKE_INTERNAL": EnvVarInfo(secret=False, overridden=True),
            "STEPUP_FAKE_KEY": EnvVarInfo(secret=True, overridden=True),
        },
    )


def _parse_token(value: str) -> str:
    if not value.startswith("tok-"):
        raise ValueError(f"not a token: {value!r}")
    return value


def _parse_mode(value: str) -> str:
    if value not in ("fast", "slow"):
        raise ValueError(f"must be fast or slow, got {value!r}")
    return value


def another_env_vars() -> EnvVarRegistry:
    return EnvVarRegistry("Another Package", {"STEPUP_ANOTHER_X": EnvVarInfo(secret=False)})


def _run_config_with_fakes(monkeypatch, path_tmp: Path, env: dict[str, str]) -> int:
    """Run `stepup config` with the fake registries of this module and the given variables."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    add_env_var_entry_points(
        monkeypatch,
        {
            "fake": f"{__name__}:fake_env_vars",
            "another": f"{__name__}:another_env_vars",
        },
    )
    return _run_main(monkeypatch, path_tmp, ["config"])


def test_cli_config_groups_extension_env_vars(monkeypatch, path_tmp, capsys, clean_env):
    """Each package has a group of its own, while the overridden variables share one."""
    env = {
        "STEPUP_FAKE_MODE": "fast",
        "STEPUP_ANOTHER_X": "1",
        "STEPUP_FAKE_INTERNAL": "2",
        "STEPUP_JOB_I": "3",
    }
    assert _run_config_with_fakes(monkeypatch, path_tmp, env) == 0
    lines = capsys.readouterr().out.splitlines()
    core = lines.index("# StepUp Core environment variables:")
    another = lines.index("# Another Package environment variables:")
    fake = lines.index("# StepUp Fake environment variables:")
    internal = lines.index(
        "# Internal environment variables, overruled by StepUp (probably a mistake):"
    )
    assert core < another < fake < internal
    assert lines[another + 1] == '#   STEPUP_ANOTHER_X = "1"'
    assert lines[fake + 1] == '#   STEPUP_FAKE_MODE = "fast"'
    assert lines[internal + 1 : internal + 3] == [
        '#   STEPUP_FAKE_INTERNAL = "2"',
        '#   STEPUP_JOB_I = "3"',
    ]


def test_cli_config_masks_secrets(monkeypatch, path_tmp, capsys, clean_env):
    """The value of a secret is never printed, whether it is registered or misspelled."""
    env = {
        "STEPUP_FAKE_TOKEN": "tok-hunter2",
        "STEPUP_FAKE_KEY": "hunter3",
        "STEPUP_FAKE_TKEN": "tok-hunter4",
    }
    assert _run_config_with_fakes(monkeypatch, path_tmp, env) == 0
    captured = capsys.readouterr()
    assert "hunter" not in captured.out
    assert captured.err == ""
    lines = captured.out.splitlines()
    assert "#   STEPUP_FAKE_TOKEN = <set, hidden>" in lines
    assert "#   STEPUP_FAKE_KEY = <set, hidden>" in lines
    unknown = lines.index("# Unrecognized environment variables, without effect:")
    assert lines[unknown + 1] == (
        "#   STEPUP_FAKE_TKEN = <set, hidden> "
        "(did you mean 'STEPUP_FAKE_TOKEN' or 'STEPUP_FAKE_KEY'?)"
    )


def test_cli_config_shows_settings_in_clear(monkeypatch, path_tmp, capsys, clean_env):
    """A setting is shown in clear, because its value also appears in its section."""
    assert _run_config_with_fakes(monkeypatch, path_tmp, {"STEPUP_BUILD_JOBS": "7"}) == 0
    lines = capsys.readouterr().out.splitlines()
    assert 'jobs = "7"  # $STEPUP_BUILD_JOBS' in lines
    assert '#   STEPUP_BUILD_JOBS = "7"' in lines


def test_cli_config_unrecognized_without_hint(monkeypatch, path_tmp, capsys, clean_env):
    """A name that is not close to any known one gets no hint."""
    assert _run_config_with_fakes(monkeypatch, path_tmp, {"STEPUP_XYZZY": "1"}) == 0
    lines = capsys.readouterr().out.splitlines()
    unknown = lines.index("# Unrecognized environment variables, without effect:")
    assert lines[unknown + 1] == "#   STEPUP_XYZZY = <set, hidden>"


def test_cli_config_inlines_rejected_values(monkeypatch, path_tmp, capsys, clean_env):
    """A rejected value is reported on the line of its variable, without leaking a secret."""
    env = {"STEPUP_FAKE_MODE": "medium", "STEPUP_FAKE_TOKEN": "hunter2"}
    assert _run_config_with_fakes(monkeypatch, path_tmp, env) == ReturnCode.INTERNAL.value
    captured = capsys.readouterr()
    assert "hunter2" not in captured.out
    assert captured.err == ""
    assert _find_line(captured.out, "#   STEPUP_FAKE_MODE = ") == (
        "#   STEPUP_FAKE_MODE = \"medium\"  <-- ERROR: must be fast or slow, got 'medium'"
    )
    assert _find_line(captured.out, "#   STEPUP_FAKE_TOKEN = ") == (
        "#   STEPUP_FAKE_TOKEN = <set, hidden>  <-- ERROR: invalid value"
    )


def test_cli_config_reports_broken_entry_point(monkeypatch, path_tmp, capsys, clean_env):
    """A broken entry point has no line of its own, so it is reported on standard error."""
    add_env_var_entry_points(monkeypatch, {"gone": "no_such_module_for_stepup:env_vars"})
    assert _run_main(monkeypatch, path_tmp, ["config"]) == ReturnCode.INTERNAL.value
    captured = capsys.readouterr()
    assert "<-- ERROR" not in captured.out
    assert "entry point 'gone = no_such_module_for_stepup:env_vars': " in captured.err
    assert "cannot be loaded: ModuleNotFoundError: " in captured.err
