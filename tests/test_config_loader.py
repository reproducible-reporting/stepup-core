# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.config_loader."""

import argparse
import os
from decimal import Decimal

import pytest
from path import Path

from stepup.core.__main__ import _setup_cli
from stepup.core.config_loader import ConfigFile, ConfigLoader, ConfigLocation, _SectionView
from stepup.core.exceptions import ConfigError, ConsistencyError

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
def render_jinja_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser("render-jinja")
    p.add_argument("--mode", choices=["auto", "plain", "latex"], default="auto")
    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def messages(loader: ConfigLoader) -> list[str]:
    """Return the message of every problem the loader found, which is what most tests assert on."""
    return [problem.message for problem in loader.problems()]


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
    """An absent file is `None`, which sets it apart from a file without settings."""
    assert loader._load_file(path_tmp / "nonexistent.toml") is None


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
    assert loader._configs == [ConfigFile(cfg, {"jobs": 8}, True)]


def test_configs_one_dict_per_paths(path_tmp: Path):
    a = path_tmp / "a.toml"
    a.write_bytes(b"jobs = 4\n")
    b = path_tmp / "b.toml"
    b.write_bytes(b"jobs = 8\n")
    loader = ConfigLoader("stepup", config_paths=[a, b], environ={})
    assert loader._configs == [ConfigFile(a, {"jobs": 4}, True), ConfigFile(b, {"jobs": 8}, True)]


def test_configs_missing_stem_gives_empty_dict():
    cfg = "/nonexistent/stepup"
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    assert loader._configs == [ConfigFile(cfg, {}, False)]


def test_configs_pyproject_auto_section(path_tmp: Path):
    cfg = path_tmp / "pyproject.toml"
    cfg.write_bytes(b"[tool.stepup]\njobs = 2\n[tool.stepup.build]\nclean = false\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    assert loader._configs == [ConfigFile(cfg, {"jobs": 2, "build": {"clean": False}}, True)]


def test_env_preloaded_at_construction():
    loader = ConfigLoader("stepup", environ={"STEPUP_JOBS": "8", "OTHER": "x"})
    assert loader._env == {"STEPUP_JOBS": "8", "OTHER": "x"}


# ---------------------------------------------------------------------------
# _section_views
# ---------------------------------------------------------------------------


def test_section_views_one_per_file_flattened(path_tmp: Path):
    a = path_tmp / "a.toml"
    a.write_bytes(b'[plugin]\nquality = "high"\n')
    b = path_tmp / "b.toml"
    b.write_bytes(b"jobs = 4\n[plugin]\nnum_jobs = 8\n[plugin.deeper]\nkey = 1\n")
    loader = ConfigLoader("stepup", config_paths=[a, b], environ={})
    assert loader._section_views("plugin") == [
        _SectionView(a, {"quality": "high"}),
        _SectionView(b, {"num_jobs": 8}),
    ]
    assert loader._table_keys == [ConfigLocation.of_setting(b, "plugin", "deeper")]


def test_section_views_top_level_keeps_sections_out(path_tmp: Path):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"jobs = 4\n[plugin]\nquality = 'high'\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    # A table at the top level is the section of another parser, not an unsupported key.
    assert loader._section_views(None) == [_SectionView(cfg, {"jobs": 4})]
    assert loader._table_keys == []


def test_section_views_missing_section_is_empty(path_tmp: Path):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"jobs = 4\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    assert loader._section_views("plugin") == [_SectionView(cfg, {})]


def test_section_views_section_holding_a_value(path_tmp: Path):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"plugin = 3\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    assert loader._section_views("plugin") == [_SectionView(cfg, {})]
    assert messages(loader) == [
        f"{cfg}: 'plugin' is configured as a value, but a section [plugin] is expected"
    ]


# ---------------------------------------------------------------------------
# patch_parser — basic injection
# ---------------------------------------------------------------------------


def test_patch_parser_from_file(path_tmp, parser, loader):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"jobs = 8\n")
    loader._configs = [ConfigFile(cfg, loader._load_file(cfg), True)]
    loader.patch_parser(parser, top_level=True)
    assert parser.parse_args([]).jobs == 8


def test_patch_parser_later_file_wins(path_tmp, parser):
    a = path_tmp / "a.toml"
    a.write_bytes(b"jobs = 4\n")
    b = path_tmp / "b.toml"
    b.write_bytes(b"jobs = 8\n")
    loader = ConfigLoader("stepup", config_paths=[a, b], environ={})
    loader.patch_parser(parser, top_level=True)
    assert parser.parse_args([]).jobs == 8


def test_patch_parser_cli_still_wins(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"jobs = 8\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, top_level=True)
    assert parser.parse_args(["--jobs", "16"]).jobs == 16


def test_patch_parser_unsupported_config_key(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"unsupported_key = 42\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, top_level=True)
    assert messages(loader) == [f"{cfg}: unsupported key 'unsupported_key' at the top level"]


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
    loader.patch_parser(parser, top_level=True)
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
    loader.patch_parser(parser, top_level=True)
    assert parser.parse_args([]).jobs == 12
    assert isinstance(parser.parse_args([]).jobs, Decimal)


def test_patch_parser_env_bool_flag_true(parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_DEBUG": "yes"})
    loader.patch_parser(parser, top_level=True)
    assert parser.parse_args([]).debug is True


def test_patch_parser_env_bool_flag_false(parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_DEBUG": "0"})
    loader.patch_parser(parser, top_level=True)
    assert parser.parse_args([]).debug is False


def test_patch_parser_env_bool_optional_action():
    p = argparse.ArgumentParser()
    p.add_argument("--clean", action=argparse.BooleanOptionalAction, default=True)
    loader = ConfigLoader("app", environ={"APP_CLEAN": "no"})
    loader.patch_parser(p, top_level=True)
    assert p.parse_args([]).clean is False


def test_patch_parser_env_count_action():
    p = argparse.ArgumentParser()
    p.add_argument("--verbose", "-v", action="count", default=0)
    loader = ConfigLoader("app", environ={"APP_VERBOSE": "2"})
    loader.patch_parser(p, top_level=True)
    assert p.parse_args(["-v"]).verbose == 3  # 2 (injected) + 1 (from -v)


def test_patch_parser_excludes_help_and_version():
    """`--help` and `--version` act while parsing and configure nothing."""
    p = argparse.ArgumentParser("app")
    p.add_argument("-V", "--version", action="version", version="1.0")
    p.add_argument("--jobs", type=int, default=1)
    loader = ConfigLoader("app", environ={"APP_VERSION": "9", "APP_JOBS": "4"})
    loader.patch_parser(p, top_level=True)
    assert loader.recognized_env_vars() == {"APP_JOBS"}
    config = loader.effective_config()
    assert set(config[None]) == {"jobs"}
    assert config[None]["jobs"].value == 4


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
    loader.patch_parser(p, top_level=True)
    args = p.parse_args([])
    assert args.targets == []
    assert args.jobs == 4


def test_patch_parser_nargs_optional_env_overrides_const():
    # Env var sets both const and default, enabling the feature without --perf.
    p = argparse.ArgumentParser()
    p.add_argument("--perf", default=None, nargs="?", const="500")
    loader = ConfigLoader("app", environ={"APP_PERF": "1000"})
    loader.patch_parser(p, top_level=True)
    assert p.parse_args([]).perf == "1000"  # feature enabled by default
    assert p.parse_args(["--perf"]).perf == "1000"  # bare flag uses overridden const
    assert p.parse_args(["--perf", "2000"]).perf == "2000"  # explicit CLI value still wins


def test_patch_parser_nargs_optional_file_overrides_const(path_tmp):
    cfg = path_tmp / "app.toml"
    cfg.write_bytes(b'perf = "1000"\n')
    p = argparse.ArgumentParser()
    p.add_argument("--perf", default=None, nargs="?", const="500")
    loader = ConfigLoader("app", config_paths=[cfg], environ={})
    loader.patch_parser(p, top_level=True)
    assert p.parse_args([]).perf == "1000"  # feature enabled by default
    assert p.parse_args(["--perf"]).perf == "1000"  # bare flag uses overridden const


def test_patch_parser_env_overrides_file(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"jobs = 2\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={"STEPUP_JOBS": "8"})
    loader.patch_parser(parser, top_level=True)
    assert parser.parse_args([]).jobs == 8
    assert isinstance(parser.parse_args([]).jobs, Decimal)


def test_patch_parser_env_unknown_vars_ignored(parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_UNKNOWN": "x", "OTHER": "y"})
    loader.patch_parser(parser, top_level=True)
    assert parser.parse_args([]).jobs == Decimal("1.2")  # unchanged default
    assert isinstance(parser.parse_args([]).jobs, Decimal)


def test_patch_parser_env_type(parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_JOBS": "12"})
    loader.patch_parser(parser, top_level=True)
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
        parser, top_level=True, merge_handlers={"search_paths": lambda a, b: f"{a}:{b}"}
    )
    assert parser.parse_args([]).search_paths == "/usr/share:/home/user/lib"


def test_patch_parser_merge_handler_two_files(path_tmp, parser):
    a = path_tmp / "a.toml"
    a.write_bytes(b'search_paths = "/usr/share"\n')
    b = path_tmp / "b.toml"
    b.write_bytes(b'search_paths = "/opt"\n')
    loader = ConfigLoader("stepup", config_paths=[a, b], environ={})
    loader.patch_parser(
        parser, top_level=True, merge_handlers={"search_paths": lambda a, b: f"{a}:{b}"}
    )
    assert parser.parse_args([]).search_paths == "/usr/share:/opt"


def test_patch_parser_merge_handler_only_env(parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_SEARCH_PATHS": "/home/user/lib"})
    loader.patch_parser(
        parser, top_level=True, merge_handlers={"search_paths": lambda a, b: f"{a}:{b}"}
    )
    # No file value, so handler is not invoked; env value used directly.
    assert parser.parse_args([]).search_paths == "/home/user/lib"


def test_patch_parser_merge_handler_only_file(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'search_paths = "/usr/share"\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(
        parser, top_level=True, merge_handlers={"search_paths": lambda a, b: f"{a}:{b}"}
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
    loader.patch_parser(parser, top_level=True)
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
    loader.patch_parser(p, top_level=True)
    assert p.parse_args([]).mode == "slow"


def test_patch_parser_choices_invalid_from_file(path_tmp):
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["fast", "slow"], default="fast")
    cfg = path_tmp / "app.toml"
    cfg.write_bytes(b'mode = "turbo"\n')
    loader = ConfigLoader("app", config_paths=[cfg], environ={})
    loader.patch_parser(p, top_level=True)
    (message,) = messages(loader)
    assert message.startswith(f"{cfg}: mode at the top level: ")
    assert "'turbo'" in message
    # The parser keeps its own default, so the rejected value cannot reach the tool.
    assert p.parse_args([]).mode == "fast"


def test_patch_parser_choices_valid_from_env():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["fast", "slow"], default="fast")
    loader = ConfigLoader("app", environ={"APP_MODE": "slow"})
    loader.patch_parser(p, top_level=True)
    assert p.parse_args([]).mode == "slow"


def test_patch_parser_choices_invalid_from_env():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["fast", "slow"], default="fast")
    loader = ConfigLoader("app", environ={"APP_MODE": "turbo"})
    loader.patch_parser(p, top_level=True)
    (message,) = messages(loader)
    assert message.startswith("$APP_MODE: ")
    assert "'turbo'" in message
    assert p.parse_args([]).mode == "fast"


# ---------------------------------------------------------------------------
# _patches tracking
# ---------------------------------------------------------------------------


def test_patches_recorded_no_section(parser, loader):
    loader.patch_parser(parser, top_level=True)
    assert list(loader._patches) == [None]
    assert "jobs" in loader._patches[None].actions


def test_patches_recorded_with_section(parser, loader):
    parser.prog = "build"
    loader.patch_parser(parser)
    assert list(loader._patches) == ["build"]
    assert "jobs" in loader._patches["build"].actions


def test_patches_accumulate_across_calls(parser, plugin_parser, loader):
    loader.patch_parser(parser, top_level=True)
    loader.patch_parser(plugin_parser)
    assert list(loader._patches) == [None, "plugin"]


def test_patches_of_aliased_parsers_merge(parser, plugin_parser, loader):
    """Two parsers sharing a section contribute to a single record, the first action winning."""
    alias_parser = argparse.ArgumentParser("plugin")
    alias_parser.add_argument("--quality", default=None)
    alias_parser.add_argument("--extra", default=None)
    loader.patch_parser(plugin_parser)
    quality = loader._patches["plugin"].actions["quality"]
    loader.patch_parser(alias_parser)
    assert list(loader._patches) == ["plugin"]
    assert loader._patches["plugin"].actions["quality"] is quality
    assert "extra" in loader._patches["plugin"].actions


def test_patches_recorded_despite_error(path_tmp, parser):
    """A parser with a bad config is still recorded, so `config` can report on it."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"unknown_key = 1\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, top_level=True)
    assert list(loader._patches) == [None]
    assert len(messages(loader)) == 1


# ---------------------------------------------------------------------------
# env_prefix, prefixed_env_vars and recognized_env_vars
# ---------------------------------------------------------------------------


def test_env_prefix():
    assert ConfigLoader("stepup", environ={}).env_prefix == "STEPUP_"
    assert ConfigLoader("app", environ={}).env_prefix == "APP_"


def test_prefixed_env_vars_selects_by_prefix():
    loader = ConfigLoader(
        "stepup",
        environ={"STEPUP_JOBS": "8", "STEPUP_BUILD_JOBS": "4", "STEPUPX": "1", "PATH": "/bin"},
    )
    assert loader.prefixed_env_vars() == {"STEPUP_JOBS": "8", "STEPUP_BUILD_JOBS": "4"}


def test_prefixed_env_vars_ignores_patched_parsers(parser):
    """The listing is about the environment, not about what a parser recognizes."""
    loader = ConfigLoader("stepup", environ={"STEPUP_NOSUCH": "1"})
    loader.patch_parser(parser, top_level=True)
    assert loader.prefixed_env_vars() == {"STEPUP_NOSUCH": "1"}


def test_recognized_env_vars_empty_before_patch(loader):
    assert loader.recognized_env_vars() == set()


def test_recognized_env_vars_includes_unset_ones(parser, plugin_parser, loader):
    """A recognized name does not have to be set, unlike one in `effective_config`."""
    loader.patch_parser(parser, top_level=True)
    loader.patch_parser(plugin_parser)
    assert loader.recognized_env_vars() == {
        "STEPUP_JOBS",
        "STEPUP_DEBUG",
        "STEPUP_LABEL",
        "STEPUP_SEARCH_PATHS",
        "STEPUP_RESOURCES",
        "STEPUP_PLUGIN_QUALITY",
        "STEPUP_PLUGIN_NUM_JOBS",
    }


# ---------------------------------------------------------------------------
# effective_config
# ---------------------------------------------------------------------------


def test_effective_config_top_level_and_sections(path_tmp, parser, plugin_parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'jobs = 8\n[plugin]\nquality = "high"\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, top_level=True)
    loader.patch_parser(plugin_parser)
    config = loader.effective_config()
    assert set(config) == {None, "plugin"}
    assert config[None]["jobs"].value == 8
    assert config[None]["jobs"].location == ConfigLocation.of_setting(cfg, None, "jobs")
    assert config["plugin"]["quality"].value == "high"
    assert config["plugin"]["quality"].location == ConfigLocation.of_setting(
        cfg, "plugin", "quality"
    )


def test_effective_config_later_file_wins(path_tmp, parser):
    low = path_tmp / "low.toml"
    low.write_bytes(b"jobs = 1\n")
    high = path_tmp / "high.toml"
    high.write_bytes(b"jobs = 2\n")
    loader = ConfigLoader("stepup", config_paths=[low, high], environ={})
    loader.patch_parser(parser, top_level=True)
    entry = loader.effective_config()[None]["jobs"]
    assert entry.value == 2
    assert entry.location == ConfigLocation.of_setting(high, None, "jobs")


def test_effective_config_env_wins_over_file(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'label = "from-file"\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={"STEPUP_LABEL": "from-env"})
    loader.patch_parser(parser, top_level=True)
    entry = loader.effective_config()[None]["label"]
    assert entry.value == "from-env"
    assert entry.location == ConfigLocation.of_env("STEPUP_LABEL")


def test_effective_config_empty_before_patch():
    """Nothing is in effect as long as no parser has claimed anything."""
    loader = ConfigLoader("stepup", environ={"STEPUP_JOBS": "8"})
    assert loader.effective_config() == {}


def test_effective_config_coerces_every_source(path_tmp, parser):
    """A value is reported in the type the parser will use, wherever it comes from."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"jobs = 8\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, top_level=True)
    assert loader.effective_config()[None]["jobs"].value == Decimal("8")

    loader = ConfigLoader("stepup", config_paths=[cfg], environ={"STEPUP_JOBS": "1.5"})
    loader.patch_parser(parser, top_level=True)
    assert loader.effective_config()[None]["jobs"].value == Decimal("1.5")


def test_effective_config_applies_merge_handler(path_tmp, parser):
    """What a merge handler builds up is what the effective configuration reports."""
    low = path_tmp / "low.toml"
    low.write_bytes(b'search_paths = "/usr/share"\n')
    high = path_tmp / "high.toml"
    high.write_bytes(b'search_paths = "/usr/local/share"\n')
    loader = ConfigLoader(
        "stepup", config_paths=[low, high], environ={"STEPUP_SEARCH_PATHS": "/lib"}
    )
    loader.patch_parser(
        parser, top_level=True, merge_handlers={"search_paths": lambda a, b: f"{a}:{b}"}
    )
    entry = loader.effective_config()[None]["search_paths"]
    assert entry.value == "/usr/share:/usr/local/share:/lib"
    assert entry.value == parser.parse_args([]).search_paths
    # The source of highest priority is the one to look at first when the value surprises.
    assert entry.location == ConfigLocation.of_env("STEPUP_SEARCH_PATHS")


def test_effective_config_skips_rejected_file_value(path_tmp):
    """A value the parser refuses is not in effect, so the file value is reported instead."""
    p = argparse.ArgumentParser("stepup")
    p.add_argument("--mode", choices=["fast", "slow"], default="fast")
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'mode = "turbo"\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(p, top_level=True)
    entry = loader.effective_config()[None]["mode"]
    assert entry.value == "turbo"
    assert entry.location == ConfigLocation.of_setting(cfg, None, "mode")
    assert len(loader.problems()) == 1


def test_effective_config_env_only_setting_of_a_section():
    """A setting that only an environment variable provides lands in its own section."""
    loader = ConfigLoader("stepup", environ={"STEPUP_BUILD_JOBS": "8"})
    p = argparse.ArgumentParser("build")
    p.add_argument("--jobs", dest="jobs", type=int)
    loader.patch_parser(p)
    entry = loader.effective_config()["build"]["jobs"]
    assert entry.value == 8
    assert entry.location == ConfigLocation.of_env("STEPUP_BUILD_JOBS")


def test_effective_config_omits_unset_settings(parser, loader):
    """An argument that no source sets keeps its own default and is not reported."""
    loader.patch_parser(parser, top_level=True)
    assert loader.effective_config() == {}


def test_effective_config_skips_uncoercible_env_value(path_tmp, parser):
    """A value that cannot be coerced is a problem, and there is nothing to render for it."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"jobs = 8\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={"STEPUP_JOBS": "abc"})
    loader.patch_parser(parser, top_level=True)
    entry = loader.effective_config()[None]["jobs"]
    assert entry.location == ConfigLocation.of_setting(cfg, None, "jobs")
    assert len(loader.problems()) == 1


def test_effective_config_ignores_deeper_nesting(path_tmp, plugin_parser):
    """Only one level of sections is configurable, so deeper tables are left out."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'[plugin]\nquality = "high"\n[plugin.deeper]\nx = 1\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(plugin_parser)
    assert set(loader.effective_config()["plugin"]) == {"quality"}


def test_effective_config_includes_unsupported_keys(path_tmp, parser):
    """Rendering shows what is in the files, so a key no parser claims is included as well."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"bogus = 1\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, top_level=True)
    assert loader.effective_config()[None]["bogus"].value == 1


# ---------------------------------------------------------------------------
# problems
# ---------------------------------------------------------------------------


def test_problems_sound_config(path_tmp, parser, plugin_parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'jobs = 8\n[plugin]\nquality = "high"\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, top_level=True)
    loader.patch_parser(plugin_parser)
    assert messages(loader) == []


def test_problems_reports_all_problems_at_once(path_tmp, parser, plugin_parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'bogus = 1\n[plugin]\nnum_jobs = "abc"\n[nosuch]\nx = 1\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, top_level=True)
    loader.patch_parser(plugin_parser)
    assert messages(loader) == [
        f"{cfg}: num_jobs in section [plugin]: invalid literal for int() with base 10: 'abc'",
        f"{cfg}: unsupported key 'bogus' at the top level",
        f"{cfg}: unknown section [nosuch]",
    ]


def test_problems_invalid_toml_syntax(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"jobs = = 4\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, top_level=True)
    (message,) = messages(loader)
    assert message.startswith(f"{cfg}: invalid TOML syntax: ")


def test_problems_section_not_a_table(path_tmp, parser, plugin_parser):
    """A section given a scalar value is reported once, as a section and not as a key."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"plugin = 3\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, top_level=True)
    loader.patch_parser(plugin_parser)
    assert messages(loader) == [
        f"{cfg}: 'plugin' is configured as a value, but a section [plugin] is expected"
    ]


def test_problems_nested_table_in_section(path_tmp, plugin_parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"[plugin.extra]\nquality = 1\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(plugin_parser)
    assert messages(loader) == [f"{cfg}: unsupported key 'extra' in section [plugin]"]


def test_problems_key_in_wrong_section(path_tmp, parser, plugin_parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"[plugin]\nlabel = 3\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, top_level=True)
    loader.patch_parser(plugin_parser)
    assert messages(loader) == [
        f"{cfg}: unsupported key 'label' in section [plugin] (it belongs at the top level)"
    ]


def test_problems_positional_argument(path_tmp, plugin_parser):
    """A positional argument is CLI-only, which the message says instead of just 'unsupported'."""
    plugin_parser.add_argument("paths", nargs="*")
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'[plugin]\npaths = ["sub/"]\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(plugin_parser)
    assert messages(loader) == [
        f"{cfg}: unsupported key 'paths' in section [plugin] "
        "(a positional command-line argument, which cannot be configured)"
    ]


def test_problems_misspelled_key(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"jbos = 3\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, top_level=True)
    assert messages(loader) == [
        f"{cfg}: unsupported key 'jbos' at the top level (did you mean 'jobs'?)"
    ]


def test_problems_misspelled_key_of_other_section(path_tmp, parser, plugin_parser):
    """A key close to one of another section is suggested together with that section."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"quailty = 3\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, top_level=True)
    loader.patch_parser(plugin_parser)
    assert messages(loader) == [
        f"{cfg}: unsupported key 'quailty' at the top level "
        "(did you mean 'quality' in section [plugin]?)"
    ]


def test_problems_misspelled_section(path_tmp, parser, plugin_parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"[plugni]\nquality = 3\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, top_level=True)
    loader.patch_parser(plugin_parser)
    assert messages(loader) == [f"{cfg}: unknown section [plugni] (did you mean 'plugin'?)"]


def test_problems_setting_written_as_section(path_tmp, parser, plugin_parser):
    """A setting written as a table is reported as such, not as an unknown section."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"[label]\nx = 1\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, top_level=True)
    loader.patch_parser(plugin_parser)
    assert messages(loader) == [
        f"{cfg}: 'label' is configured as a section, but a value is expected at the top level"
    ]


def test_problems_setting_of_other_section_written_as_section(path_tmp, parser, plugin_parser):
    """The message names the section the setting belongs to, wherever it was written."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"[quality]\nx = 1\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, top_level=True)
    loader.patch_parser(plugin_parser)
    assert messages(loader) == [
        f"{cfg}: 'quality' is configured as a section, but a value is expected in section [plugin]"
    ]


def test_problems_pyproject_locations(path_tmp, parser, plugin_parser):
    cfg = path_tmp / "pyproject.toml"
    cfg.write_bytes(b"[tool.stepup]\nbogus = 1\n[tool.stepup.nosuch]\nx = 1\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, top_level=True)
    loader.patch_parser(plugin_parser)
    assert messages(loader) == [
        f"{cfg}: unsupported key 'bogus' in section [tool.stepup]",
        f"{cfg}: unknown section [tool.stepup.nosuch]",
    ]


def test_problems_deduplicates_aliased_parsers(path_tmp, plugin_parser):
    """Two subcommands sharing a config section must not report the same problem twice."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"[plugin]\nbogus = 1\n")
    alias_parser = argparse.ArgumentParser("plugin")
    alias_parser.add_argument("--quality", default=None)
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(plugin_parser)
    loader.patch_parser(alias_parser)
    assert messages(loader) == [f"{cfg}: unsupported key 'bogus' in section [plugin]"]


def test_problems_key_of_aliased_parser_only(path_tmp, plugin_parser):
    """A key that only one of two parsers sharing a section defines is still supported."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"[plugin]\nextra = 1\n")
    alias_parser = argparse.ArgumentParser("plugin")
    alias_parser.add_argument("--extra", type=int, default=None)
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(plugin_parser)
    loader.patch_parser(alias_parser)
    assert messages(loader) == []
    assert alias_parser.parse_args([]).extra == 1


def test_problems_table_where_a_setting_is_expected(path_tmp, plugin_parser):
    """A table is never a setting, not even under the name of one the section supports."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"[plugin.quality]\nx = 1\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(plugin_parser)
    assert messages(loader) == [f"{cfg}: unsupported key 'quality' in section [plugin]"]


def test_problems_short_path_in_working_directory(path_tmp, parser, monkeypatch):
    """A config file below the working directory is named as in the config header."""
    monkeypatch.chdir(path_tmp)
    cfg = Path(os.getcwd()) / "stepup.toml"
    cfg.write_bytes(b"bogus = 1\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, top_level=True)
    assert messages(loader) == ["./stepup.toml: unsupported key 'bogus' at the top level"]


def test_problems_env_var_problem(parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_JOBS": "abc"})
    loader.patch_parser(parser, top_level=True)
    (message,) = messages(loader)
    assert message.startswith("$STEPUP_JOBS: ")


def test_problem_location(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"bogus = 1\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={"STEPUP_JOBS": "abc"})
    loader.patch_parser(parser, top_level=True)
    env_problem, file_problem = loader.problems()
    assert file_problem.location == ConfigLocation.of_setting(cfg, None, "bogus")
    assert str(file_problem.location) == str(cfg)
    assert file_problem.message == f"{cfg}: {file_problem.detail}"
    assert env_problem.location == ConfigLocation.of_env("STEPUP_JOBS")
    assert env_problem.location.path is None
    assert str(env_problem.location) == "$STEPUP_JOBS"


def test_location_without_source_rejected():
    """A location that names neither a config file nor an env var cannot be shown."""
    with pytest.raises(ConsistencyError):
        ConfigLocation()


def test_locations_of_different_kinds_differ(path_tmp):
    """The four kinds of location never collide, which is what keeps them usable as keys."""
    cfg = path_tmp / "stepup.toml"
    locations = {
        ConfigLocation.of_file(cfg),
        ConfigLocation.of_section(cfg, "build"),
        ConfigLocation.of_setting(cfg, "build", "jobs"),
        ConfigLocation.of_setting(cfg, None, "jobs"),
        ConfigLocation.of_env("STEPUP_BUILD_JOBS"),
    }
    assert len(locations) == 5
    assert ConfigLocation.of_file(str(cfg)) in locations


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
        parser, top_level=True, merge_handlers={"search_paths": lambda a, b: f"{a}:{b}"}
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
