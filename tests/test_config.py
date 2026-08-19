# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.config."""

import argparse
import os
import sys
import tomllib
from decimal import Decimal

import pytest
from path import Path

from stepup.core.__main__ import _setup_cli, main
from stepup.core.config import CORE_ENV_VARS, ConfigLoader
from stepup.core.enums import ReturnCode
from stepup.core.exceptions import ConfigError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def loader() -> ConfigLoader:
    # environ={} prevents real env vars from leaking into tests.
    return ConfigLoader("stepup", environ={})


@pytest.fixture
def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser("stepup")
    p.add_argument("--jobs", dest="jobs", type=Decimal, default=Decimal("1.2"))
    p.add_argument("--debug", action="store_true", default=False)
    p.add_argument("--label", default=None)
    p.add_argument("--search-paths", dest="search_paths", default=None)
    p.add_argument("--resources", default="")
    return p


@pytest.fixture
def plugin_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser("plugin")
    p.add_argument("--quality", default=None)
    p.add_argument("--num-jobs", dest="num_jobs", type=int, default=1)
    return p


@pytest.fixture
def clean_env(monkeypatch, path_tmp):
    """Hide the developer's own configuration, so it cannot reach the output under test.

    Both the StepUp environment variables and `~/.config/stepup.toml` are put out of reach.
    The system-wide `/etc/stepup.toml` is the one config file that cannot be hidden this way.
    """
    for name in list(os.environ):
        if name.startswith("STEPUP_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", path_tmp)


@pytest.fixture
def render_jinja_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser("render-jinja")
    p.add_argument("--mode", choices=["auto", "plain", "latex"], default="auto")
    return p


# ---------------------------------------------------------------------------
# _load_file
# ---------------------------------------------------------------------------


def test_load_file_toml(path_tmp: Path, loader: ConfigLoader):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'jobs = 8\nlabel = "prod"\n')
    assert loader._load_file(cfg) == {"jobs": 8, "label": "prod"}


def test_load_file_pyproject_toml(path_tmp: Path, loader: ConfigLoader):
    cfg = path_tmp / "pyproject.toml"
    cfg.write_bytes(b'[tool.stepup]\njobs = 2\nlabel = "proj"\n')
    assert loader._load_file(
        cfg,
    ) == {"jobs": 2, "label": "proj"}


def test_load_file_pyproject_toml_section_missing(path_tmp: Path, loader: ConfigLoader):
    cfg = path_tmp / "pyproject.toml"
    cfg.write_bytes(b"[tool.other]\njobs = 2\n")
    assert loader._load_file(cfg) == {}


def test_load_file_missing(path_tmp: Path, loader: ConfigLoader):
    assert loader._load_file(path_tmp / "nonexistent.toml") == {}


def test_load_file_unsupported_format(path_tmp: Path, loader: ConfigLoader):
    cfg = path_tmp / "stepup.ini"
    cfg.write_text("[stepup]\njobs = 4\n")
    with pytest.raises(ConfigError, match="unsupported config file format"):
        loader._load_file(cfg)


def test_load_file_invalid_toml(path_tmp: Path, loader: ConfigLoader):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"this is not toml\n")
    with pytest.raises(ConfigError, match="invalid TOML syntax"):
        loader._load_file(cfg)


def test_load_file_pyproject_section_not_a_table(path_tmp: Path, loader: ConfigLoader):
    cfg = path_tmp / "pyproject.toml"
    cfg.write_bytes(b"[tool]\nstepup = 3\n")
    with pytest.raises(ConfigError, match=r"'tool\.stepup' is configured as a value"):
        loader._load_file(cfg)


def test_load_file_tilde_expansion(path_tmp: Path, loader: ConfigLoader, monkeypatch):
    monkeypatch.setenv("HOME", str(path_tmp))
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"jobs = 7\n")
    assert loader._load_file("~/stepup.toml") == {"jobs": 7}


def test_load_file_preserves_nested_sections(path_tmp: Path, loader: ConfigLoader):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"jobs = 4\n[build]\nclean = false\n")
    assert loader._load_file(cfg) == {"jobs": 4, "build": {"clean": False}}


# ---------------------------------------------------------------------------
# _configs population
# ---------------------------------------------------------------------------


def test_configs_populated_from_paths(path_tmp: Path):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"jobs = 8\n")
    loader = ConfigLoader("stepup", config_paths=[path_tmp / "stepup.toml"], environ={})
    assert loader._configs == [(cfg, {"jobs": 8})]


def test_configs_one_dict_per_paths(path_tmp: Path):
    a = path_tmp / "a.toml"
    a.write_bytes(b"jobs = 4\n")
    b = path_tmp / "b.toml"
    b.write_bytes(b"jobs = 8\n")
    loader = ConfigLoader("stepup", config_paths=[a, b], environ={})
    assert loader._configs == [(a, {"jobs": 4}), (b, {"jobs": 8})]


def test_configs_missing_stem_gives_empty_dict():
    cfg = "/nonexistent/stepup"
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    assert loader._configs == [(Path(cfg), {})]


def test_configs_pyproject_auto_section(path_tmp: Path):
    cfg = path_tmp / "pyproject.toml"
    cfg.write_bytes(b"[tool.stepup]\njobs = 2\n[tool.stepup.build]\nclean = false\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    assert loader._configs == [(cfg, {"jobs": 2, "build": {"clean": False}})]


def test_env_preloaded_at_construction():
    loader = ConfigLoader("stepup", environ={"STEPUP_JOBS": "8", "OTHER": "x"})
    assert loader._env == {"STEPUP_JOBS": "8", "OTHER": "x"}


# ---------------------------------------------------------------------------
# patch_parser — basic injection
# ---------------------------------------------------------------------------


def test_patch_parser_from_file(path_tmp, parser, loader):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"jobs = 8\n")
    loader._configs = [(cfg, loader._load_file(cfg))]
    loader.patch_parser(parser, use_section=False)
    assert parser.parse_args([]).jobs == 8


def test_patch_parser_later_file_wins(path_tmp, parser):
    a = path_tmp / "a.toml"
    a.write_bytes(b"jobs = 4\n")
    b = path_tmp / "b.toml"
    b.write_bytes(b"jobs = 8\n")
    loader = ConfigLoader("stepup", config_paths=[a, b], environ={})
    loader.patch_parser(parser, use_section=False)
    assert parser.parse_args([]).jobs == 8


def test_patch_parser_cli_still_wins(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"jobs = 8\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, use_section=False)
    assert parser.parse_args(["--jobs", "16"]).jobs == 16


def test_patch_parser_unsupported_config_key(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"unsupported_key = 42\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, use_section=False)
    assert loader.check() == [f"{cfg}: unsupported key 'unsupported_key' at the top level"]


# ---------------------------------------------------------------------------
# patch_parser — section navigation
# ---------------------------------------------------------------------------


def test_patch_parser_section(path_tmp, plugin_parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'jobs = 8\n[plugin]\nquality = "high"\nnum_jobs = 4\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(plugin_parser)
    ns = plugin_parser.parse_args([])
    assert ns.quality == "high"
    assert ns.num_jobs == 4


def test_patch_parser_no_section_uses_top_level(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"jobs = 8\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, use_section=False)
    assert parser.parse_args([]).jobs == Decimal("8")
    assert isinstance(parser.parse_args([]).jobs, Decimal)


def test_patch_parser_missing_section_leaves_defaults(path_tmp, plugin_parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"jobs = 8\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(plugin_parser)
    ns = plugin_parser.parse_args([])
    assert ns.quality is None
    assert ns.num_jobs == 1


def test_patch_parser_section_of_real_subparser(path_tmp):
    # Argparse prefixes a subparser's prog with the parent's, e.g. "stepup plugin".
    # Only the last word may be used as section name.
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'[plugin]\nquality = "high"\n')
    main_parser = argparse.ArgumentParser(prog="stepup")
    subparsers = main_parser.add_subparsers(dest="tool")
    sub = subparsers.add_parser("plugin")
    sub.add_argument("--quality", default=None)
    sub.add_argument("--label", default=None)
    assert sub.prog == "stepup plugin"
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={"STEPUP_PLUGIN_LABEL": "x"})
    loader.patch_parser(sub)
    ns = main_parser.parse_args(["plugin"])
    assert ns.quality == "high"
    assert ns.label == "x"


def test_patch_parser_section_of_alias_subparser(path_tmp):
    # A subparser registered under an alias shares the section of the subcommand it
    # aliases by pinning its prog, so that both read the same configuration.
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'[plugin]\nquality = "high"\n')
    main_parser = argparse.ArgumentParser(prog="stepup")
    subparsers = main_parser.add_subparsers(dest="tool")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    for name in "plugin", "old-plugin":
        sub = subparsers.add_parser(name, prog="stepup plugin")
        sub.add_argument("--quality", default=None)
        loader.patch_parser(sub)
    assert main_parser.parse_args(["plugin"]).quality == "high"
    assert main_parser.parse_args(["old-plugin"]).quality == "high"


# ---------------------------------------------------------------------------
# patch_parser — environment variable overlay
# ---------------------------------------------------------------------------


def test_patch_parser_env_basic(parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_JOBS": "12"})
    loader.patch_parser(parser, use_section=False)
    assert parser.parse_args([]).jobs == 12
    assert isinstance(parser.parse_args([]).jobs, Decimal)


def test_patch_parser_env_bool_flag_true(parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_DEBUG": "yes"})
    loader.patch_parser(parser, use_section=False)
    assert parser.parse_args([]).debug is True


def test_patch_parser_env_bool_flag_false(parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_DEBUG": "0"})
    loader.patch_parser(parser, use_section=False)
    assert parser.parse_args([]).debug is False


def test_patch_parser_env_bool_optional_action():
    p = argparse.ArgumentParser()
    p.add_argument("--clean", action=argparse.BooleanOptionalAction, default=True)
    loader = ConfigLoader("app", environ={"APP_CLEAN": "no"})
    loader.patch_parser(p, use_section=False)
    assert p.parse_args([]).clean is False


def test_patch_parser_env_count_action():
    p = argparse.ArgumentParser()
    p.add_argument("--verbose", "-v", action="count", default=0)
    loader = ConfigLoader("app", environ={"APP_VERBOSE": "2"})
    loader.patch_parser(p, use_section=False)
    assert p.parse_args(["-v"]).verbose == 3  # 2 (injected) + 1 (from -v)


def test_patch_parser_excludes_positional():
    """A positional argument (no `option_strings`) must never be patched from the environment.

    Positionals have `type=None`, so a raw env-var string would otherwise be assigned
    unparsed as the default, and iterating it (e.g. `for t in args.targets`) would
    silently yield individual characters instead of the intended list of values.
    """
    p = argparse.ArgumentParser()
    p.add_argument("targets", nargs="*", default=[])
    p.add_argument("--jobs", type=int, default=1)
    loader = ConfigLoader("app", environ={"APP_TARGETS": "foo.txt", "APP_JOBS": "4"})
    loader.patch_parser(p, use_section=False)
    args = p.parse_args([])
    assert args.targets == []
    assert args.jobs == 4


def test_patch_parser_nargs_optional_env_overrides_const():
    # Env var sets both const and default, enabling the feature without --perf.
    p = argparse.ArgumentParser()
    p.add_argument("--perf", default=None, nargs="?", const="500")
    loader = ConfigLoader("app", environ={"APP_PERF": "1000"})
    loader.patch_parser(p, use_section=False)
    assert p.parse_args([]).perf == "1000"  # feature enabled by default
    assert p.parse_args(["--perf"]).perf == "1000"  # bare flag uses overridden const
    assert p.parse_args(["--perf", "2000"]).perf == "2000"  # explicit CLI value still wins


def test_patch_parser_nargs_optional_file_overrides_const(path_tmp):
    cfg = path_tmp / "app.toml"
    cfg.write_bytes(b'perf = "1000"\n')
    p = argparse.ArgumentParser()
    p.add_argument("--perf", default=None, nargs="?", const="500")
    loader = ConfigLoader("app", config_paths=[cfg], environ={})
    loader.patch_parser(p, use_section=False)
    assert p.parse_args([]).perf == "1000"  # feature enabled by default
    assert p.parse_args(["--perf"]).perf == "1000"  # bare flag uses overridden const


def test_patch_parser_env_overrides_file(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"jobs = 2\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={"STEPUP_JOBS": "8"})
    loader.patch_parser(parser, use_section=False)
    assert parser.parse_args([]).jobs == 8
    assert isinstance(parser.parse_args([]).jobs, Decimal)


def test_patch_parser_env_unknown_vars_ignored(parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_UNKNOWN": "x", "OTHER": "y"})
    loader.patch_parser(parser, use_section=False)
    assert parser.parse_args([]).jobs == Decimal("1.2")  # unchanged default
    assert isinstance(parser.parse_args([]).jobs, Decimal)


def test_patch_parser_env_type(parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_JOBS": "12"})
    loader.patch_parser(parser, use_section=False)
    assert parser.parse_args([]).jobs == 12
    assert isinstance(parser.parse_args([]).jobs, Decimal)


def test_patch_parser_env_section_prefix_required(plugin_parser):
    # The un-prefixed name is NOT matched when a section is given.
    loader = ConfigLoader("stepup", environ={"STEPUP_NUM_JOBS": "2"})
    loader.patch_parser(plugin_parser)
    assert plugin_parser.parse_args([]).num_jobs == 1  # unchanged


def test_patch_parser_env_hyphen_section(render_jinja_parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_RENDER_JINJA_MODE": "plain"})
    loader.patch_parser(render_jinja_parser)
    assert render_jinja_parser.parse_args([]).mode == "plain"


# ---------------------------------------------------------------------------
# patch_parser — merge handlers
# ---------------------------------------------------------------------------


def test_patch_parser_merge_handler_file_plus_env(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'search_paths = "/usr/share"\n')
    loader = ConfigLoader(
        "stepup",
        config_paths=[cfg],
        environ={"STEPUP_SEARCH_PATHS": "/home/user/lib"},
    )
    loader.patch_parser(
        parser, use_section=False, merge_handlers={"search_paths": lambda a, b: f"{a}:{b}"}
    )
    assert parser.parse_args([]).search_paths == "/usr/share:/home/user/lib"


def test_patch_parser_merge_handler_two_files(path_tmp, parser):
    a = path_tmp / "a.toml"
    a.write_bytes(b'search_paths = "/usr/share"\n')
    b = path_tmp / "b.toml"
    b.write_bytes(b'search_paths = "/opt"\n')
    loader = ConfigLoader("stepup", config_paths=[a, b], environ={})
    loader.patch_parser(
        parser, use_section=False, merge_handlers={"search_paths": lambda a, b: f"{a}:{b}"}
    )
    assert parser.parse_args([]).search_paths == "/usr/share:/opt"


def test_patch_parser_merge_handler_only_env(parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_SEARCH_PATHS": "/home/user/lib"})
    loader.patch_parser(
        parser, use_section=False, merge_handlers={"search_paths": lambda a, b: f"{a}:{b}"}
    )
    # No file value, so handler is not invoked; env value used directly.
    assert parser.parse_args([]).search_paths == "/home/user/lib"


def test_patch_parser_merge_handler_only_file(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'search_paths = "/usr/share"\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(
        parser, use_section=False, merge_handlers={"search_paths": lambda a, b: f"{a}:{b}"}
    )
    # No env value, so handler is not invoked; file value used directly.
    assert parser.parse_args([]).search_paths == "/usr/share"


# ---------------------------------------------------------------------------
# patch_parser — multiple parsers
# ---------------------------------------------------------------------------


def test_patch_parser_section_isolates_parsers(path_tmp, parser, plugin_parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'jobs = "1.1"\n[plugin]\nquality = "high"\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, use_section=False)
    loader.patch_parser(plugin_parser)
    assert parser.parse_args([]).jobs == Decimal("1.1")
    assert isinstance(parser.parse_args([]).jobs, Decimal)
    assert plugin_parser.parse_args([]).quality == "high"


# ---------------------------------------------------------------------------
# patch_parser — choices validation
# ---------------------------------------------------------------------------


def test_patch_parser_choices_valid_from_file(path_tmp):
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["fast", "slow"], default="fast")
    cfg = path_tmp / "app.toml"
    cfg.write_bytes(b'mode = "slow"\n')
    loader = ConfigLoader("app", config_paths=[cfg], environ={})
    loader.patch_parser(p, use_section=False)
    assert p.parse_args([]).mode == "slow"


def test_patch_parser_choices_invalid_from_file(path_tmp):
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["fast", "slow"], default="fast")
    cfg = path_tmp / "app.toml"
    cfg.write_bytes(b'mode = "turbo"\n')
    loader = ConfigLoader("app", config_paths=[cfg], environ={})
    loader.patch_parser(p, use_section=False)
    (message,) = loader.check()
    assert message.startswith(f"{cfg}: mode at the top level: ")
    assert "'turbo'" in message
    # The parser keeps its own default, so the rejected value cannot reach the tool.
    assert p.parse_args([]).mode == "fast"


def test_patch_parser_choices_valid_from_env():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["fast", "slow"], default="fast")
    loader = ConfigLoader("app", environ={"APP_MODE": "slow"})
    loader.patch_parser(p, use_section=False)
    assert p.parse_args([]).mode == "slow"


def test_patch_parser_choices_invalid_from_env():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["fast", "slow"], default="fast")
    loader = ConfigLoader("app", environ={"APP_MODE": "turbo"})
    loader.patch_parser(p, use_section=False)
    (message,) = loader.check()
    assert message.startswith("$APP_MODE: ")
    assert "'turbo'" in message
    assert p.parse_args([]).mode == "fast"


# ---------------------------------------------------------------------------
# _patches tracking
# ---------------------------------------------------------------------------


def test_patches_recorded_no_section(parser, loader):
    loader.patch_parser(parser, use_section=False)
    assert len(loader._patches) == 1
    section, actions = loader._patches[0]
    assert section is None
    assert "jobs" in actions


def test_patches_recorded_with_section(parser, loader):
    parser.prog = "build"
    loader.patch_parser(parser, use_section=True)
    section, actions = loader._patches[0]
    assert section == "build"
    assert "jobs" in actions


def test_patches_accumulate_across_calls(parser, plugin_parser, loader):
    loader.patch_parser(parser, use_section=False)
    loader.patch_parser(plugin_parser)
    assert len(loader._patches) == 2
    assert loader._patches[0][0] is None
    assert loader._patches[1][0] == "plugin"


def test_patches_recorded_despite_error(path_tmp, parser):
    """A parser with a bad config is still recorded, so `config` can report on it."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"unknown_key = 1\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, use_section=False)
    assert len(loader._patches) == 1
    assert len(loader.check()) == 1


# ---------------------------------------------------------------------------
# env_to_toml_map
# ---------------------------------------------------------------------------


def test_env_to_toml_map_empty_before_patch():
    loader = ConfigLoader("stepup", environ={"STEPUP_JOBS": "8"})
    assert loader.env_to_toml_map() == {}


def test_env_to_toml_map_no_section():
    loader = ConfigLoader("stepup", environ={"STEPUP_JOBS": "8"})
    p = argparse.ArgumentParser()
    p.add_argument("--jobs", dest="jobs", type=int)
    loader.patch_parser(p, use_section=False)
    result = loader.env_to_toml_map()
    assert result == {"STEPUP_JOBS": [(None, "jobs", 8)]}


def test_env_to_toml_map_with_section():
    loader = ConfigLoader("stepup", environ={"STEPUP_BUILD_JOBS": "8"})
    p = argparse.ArgumentParser("build")
    p.add_argument("--jobs", dest="jobs", type=int)
    loader.patch_parser(p)
    result = loader.env_to_toml_map()
    assert result == {"STEPUP_BUILD_JOBS": [("build", "jobs", 8)]}


def test_env_to_toml_map_unset_var_excluded(parser, loader):
    loader.patch_parser(parser, use_section=False)
    assert loader.env_to_toml_map() == {}


def test_env_to_toml_map_multiple_sections():
    loader = ConfigLoader(
        "stepup",
        environ={"STEPUP_JOBS": "4", "STEPUP_BUILD_LABEL": "prod"},
    )
    p_main = argparse.ArgumentParser("stepup")
    p_main.add_argument("--jobs", dest="jobs", type=int)
    p_build = argparse.ArgumentParser("build")
    p_build.add_argument("--label")
    loader.patch_parser(p_main, use_section=False)
    loader.patch_parser(p_build)
    result = loader.env_to_toml_map()
    assert result == {
        "STEPUP_JOBS": [(None, "jobs", 4)],
        "STEPUP_BUILD_LABEL": [("build", "label", "prod")],
    }


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def test_check_sound_config(path_tmp, parser, plugin_parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'jobs = 8\n[plugin]\nquality = "high"\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, use_section=False)
    loader.patch_parser(plugin_parser)
    assert loader.check() == []


def test_check_reports_all_problems_at_once(path_tmp, parser, plugin_parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'bogus = 1\n[plugin]\nnum_jobs = "abc"\n[nosuch]\nx = 1\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, use_section=False)
    loader.patch_parser(plugin_parser)
    assert loader.check() == [
        f"{cfg}: num_jobs in section [plugin]: invalid literal for int() with base 10: 'abc'",
        f"{cfg}: unsupported key 'bogus' at the top level",
        f"{cfg}: unknown section [nosuch]",
    ]


def test_check_invalid_toml_syntax(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"jobs = = 4\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, use_section=False)
    (message,) = loader.check()
    assert message.startswith(f"{cfg}: invalid TOML syntax: ")


def test_check_section_not_a_table(path_tmp, parser, plugin_parser):
    """A section given a scalar value is reported once, as a section and not as a key."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"plugin = 3\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, use_section=False)
    loader.patch_parser(plugin_parser)
    assert loader.check() == [
        f"{cfg}: 'plugin' is configured as a value, but a section [plugin] is expected"
    ]


def test_check_nested_table_in_section(path_tmp, plugin_parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"[plugin.extra]\nquality = 1\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(plugin_parser)
    assert loader.check() == [f"{cfg}: unsupported key 'extra' in section [plugin]"]


def test_check_key_in_wrong_section(path_tmp, parser, plugin_parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"[plugin]\nlabel = 3\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, use_section=False)
    loader.patch_parser(plugin_parser)
    assert loader.check() == [
        f"{cfg}: unsupported key 'label' in section [plugin] (it belongs at the top level)"
    ]


def test_check_positional_argument(path_tmp, plugin_parser):
    """A positional argument is CLI-only, which the message says instead of just 'unsupported'."""
    plugin_parser.add_argument("paths", nargs="*")
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'[plugin]\npaths = ["sub/"]\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(plugin_parser)
    assert loader.check() == [
        f"{cfg}: unsupported key 'paths' in section [plugin] "
        "(a positional command-line argument, which cannot be configured)"
    ]


def test_check_misspelled_key(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"jbos = 3\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, use_section=False)
    assert loader.check() == [
        f"{cfg}: unsupported key 'jbos' at the top level (did you mean 'jobs'?)"
    ]


def test_check_misspelled_key_of_other_section(path_tmp, parser, plugin_parser):
    """A key close to one of another section is suggested together with that section."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"quailty = 3\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, use_section=False)
    loader.patch_parser(plugin_parser)
    assert loader.check() == [
        f"{cfg}: unsupported key 'quailty' at the top level "
        "(did you mean 'quality' in section [plugin]?)"
    ]


def test_check_misspelled_section(path_tmp, parser, plugin_parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"[plugni]\nquality = 3\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, use_section=False)
    loader.patch_parser(plugin_parser)
    assert loader.check() == [f"{cfg}: unknown section [plugni] (did you mean 'plugin'?)"]


def test_check_setting_written_as_section(path_tmp, parser, plugin_parser):
    """A setting written as a table is reported as such, not as an unknown section."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"[label]\nx = 1\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, use_section=False)
    loader.patch_parser(plugin_parser)
    assert loader.check() == [
        f"{cfg}: 'label' is configured as a section, but a value is expected at the top level"
    ]


def test_check_setting_of_other_section_written_as_section(path_tmp, parser, plugin_parser):
    """The message names the section the setting belongs to, wherever it was written."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"[quality]\nx = 1\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, use_section=False)
    loader.patch_parser(plugin_parser)
    assert loader.check() == [
        f"{cfg}: 'quality' is configured as a section, but a value is expected in section [plugin]"
    ]


def test_check_pyproject_locations(path_tmp, parser, plugin_parser):
    cfg = path_tmp / "pyproject.toml"
    cfg.write_bytes(b"[tool.stepup]\nbogus = 1\n[tool.stepup.nosuch]\nx = 1\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, use_section=False)
    loader.patch_parser(plugin_parser)
    assert loader.check() == [
        f"{cfg}: unsupported key 'bogus' in section [tool.stepup]",
        f"{cfg}: unknown section [tool.stepup.nosuch]",
    ]


def test_check_deduplicates_aliased_parsers(path_tmp, plugin_parser):
    """Two subcommands sharing a config section must not report the same problem twice."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"[plugin]\nbogus = 1\n")
    alias_parser = argparse.ArgumentParser("plugin")
    alias_parser.add_argument("--quality", default=None)
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(plugin_parser)
    loader.patch_parser(alias_parser)
    assert loader.check() == [f"{cfg}: unsupported key 'bogus' in section [plugin]"]


def test_check_short_path_in_working_directory(path_tmp, parser, monkeypatch):
    """A config file below the working directory is named as in the config header."""
    monkeypatch.chdir(path_tmp)
    cfg = Path(os.getcwd()) / "stepup.toml"
    cfg.write_bytes(b"bogus = 1\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, use_section=False)
    assert loader.check() == ["./stepup.toml: unsupported key 'bogus' at the top level"]


def test_check_env_var_problem(parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_JOBS": "abc"})
    loader.patch_parser(parser, use_section=False)
    (message,) = loader.check()
    assert message.startswith("$STEPUP_JOBS: ")


def test_problem_location(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"bogus = 1\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={"STEPUP_JOBS": "abc"})
    loader.patch_parser(parser, use_section=False)
    env_problem, file_problem = loader.problems()
    assert file_problem.path == cfg
    assert file_problem.section is None
    assert file_problem.key == "bogus"
    assert file_problem.location == str(cfg)
    assert file_problem.message == f"{cfg}: {file_problem.detail}"
    assert env_problem.env_var == "STEPUP_JOBS"
    assert env_problem.path is None
    assert env_problem.location == "$STEPUP_JOBS"


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


def test_full_integration(path_tmp, parser):
    etc_cfg = path_tmp / "etc.toml"
    etc_cfg.write_bytes(b'jobs = 8\nsearch_paths = "/usr/share"\n')

    pyproject = path_tmp / "pyproject.toml"
    pyproject.write_bytes(b'[tool.stepup]\njobs = 2\nlabel = "proj"\n')

    loader = ConfigLoader(
        "stepup",
        config_paths=[etc_cfg, pyproject],
        environ={"STEPUP_DEBUG": "yes", "STEPUP_SEARCH_PATHS": "/home/user/lib"},
    )
    loader.patch_parser(
        parser, use_section=False, merge_handlers={"search_paths": lambda a, b: f"{a}:{b}"}
    )

    ns = parser.parse_args(["--label", "cli"])

    assert ns.jobs == Decimal("2")  # pyproject overrides etc
    assert isinstance(parser.parse_args([]).jobs, Decimal)
    assert ns.debug is True  # from env
    assert ns.search_paths == "/usr/share:/home/user/lib"  # merge handler
    assert ns.label == "cli"  # CLI overrides pyproject


@pytest.mark.parametrize(
    ("argv", "env_var", "env_value", "dest", "expected"),
    [
        (["build"], "STEPUP_BUILD_JOBS", "7", "jobs", Decimal("7")),
        (["build"], "STEPUP_BUILD_LOG_LEVEL", "INFO", "log_level", "INFO"),
        # The deprecated boot alias must read the build section, not a boot section.
        (["boot"], "STEPUP_BUILD_JOBS", "7", "jobs", Decimal("7")),
        (["clean", "."], "STEPUP_CLEAN_ALL", "1", "all", True),
        (["browse"], "STEPUP_BROWSE_PORT", "4242", "port", 4242),
    ],
)
def test_cli_section_per_subcommand(
    monkeypatch, path_tmp, argv, env_var, env_value, dest, expected, clean_env
):
    """Each subcommand reads the config section documented in `docs/reference/configuration.md`."""
    # Point STEPUP_ROOT at an empty directory, so that no config file of this repository
    # interferes with the environment variable under test.
    monkeypatch.setenv("STEPUP_ROOT", path_tmp)
    monkeypatch.setenv(env_var, env_value)
    parser, _ = _setup_cli()
    assert getattr(parser.parse_args(argv), dest) == expected


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


def test_cli_without_subcommand(monkeypatch, path_tmp, capsys, clean_env):
    """Without a subcommand, the help is printed and nothing is done."""
    assert _run_main(monkeypatch, path_tmp, []) == ReturnCode.INTERNAL.value
    assert "General purpose dynamic build tool." in capsys.readouterr().out


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


def test_cli_config_groups_env_vars(monkeypatch, path_tmp, capsys, clean_env):
    """A variable without effect is listed apart from the settings and the internal ones."""
    monkeypatch.setenv("STEPUP_BUILD_JOBS", "3")
    monkeypatch.setenv("STEPUP_BUILD_JBOS", "3")
    assert _run_main(monkeypatch, path_tmp, ["config"]) == 0
    lines = capsys.readouterr().out.splitlines()
    setting = lines.index("# Configuration environment variables:")
    internal = lines.index("# StepUp Core module environment variables:")
    unknown = lines.index("# Unrecognized environment variables, without effect:")
    assert setting < internal < unknown
    assert lines[setting + 1] == '#   STEPUP_BUILD_JOBS = "3"'
    assert lines[internal + 1] == f'#   STEPUP_ROOT = "{path_tmp}"'
    assert lines[unknown + 1] == '#   STEPUP_BUILD_JBOS = "3"'


def test_core_env_vars_are_not_settings(monkeypatch, path_tmp, clean_env):
    """A variable listed as core would never be shown as such once it becomes a setting."""
    monkeypatch.setenv("STEPUP_ROOT", path_tmp)
    _, loader = _setup_cli()
    assert CORE_ENV_VARS.isdisjoint(loader.known_env_vars())


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
