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
"""Tests for stepup.core.config."""

import argparse
from decimal import Decimal

import pytest
from path import Path

from stepup.core.config import ConfigLoader

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def loader() -> ConfigLoader:
    # environ={} prevents real env vars from leaking into tests.
    return ConfigLoader("stepup", environ={})


@pytest.fixture
def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--num-workers", dest="num_workers", type=Decimal, default=Decimal("1.2"))
    p.add_argument("--debug", action="store_true", default=False)
    p.add_argument("--label", default=None)
    p.add_argument("--search-paths", dest="search_paths", default=None)
    p.add_argument("--resources", default="")
    p.add_argument("--root", default="here/")
    return p


@pytest.fixture
def plugin_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--quality", default=None)
    p.add_argument("--num-jobs", dest="num_jobs", type=int, default=1)
    return p


# ---------------------------------------------------------------------------
# _load_file
# ---------------------------------------------------------------------------


def test_load_file_toml(path_tmp: Path, loader: ConfigLoader):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'num_workers = 8\nlabel = "prod"\n')
    assert loader._load_file(cfg) == {"num_workers": 8, "label": "prod"}


def test_load_file_pyproject_toml(path_tmp: Path, loader: ConfigLoader):
    cfg = path_tmp / "pyproject.toml"
    cfg.write_bytes(b'[tool.stepup]\nnum_workers = 2\nlabel = "proj"\n')
    assert loader._load_file(
        cfg,
    ) == {"num_workers": 2, "label": "proj"}


def test_load_file_pyproject_toml_section_missing(path_tmp: Path, loader: ConfigLoader):
    cfg = path_tmp / "pyproject.toml"
    cfg.write_bytes(b"[tool.other]\nnum_workers = 2\n")
    assert loader._load_file(cfg) == {}


def test_load_file_missing(path_tmp: Path, loader: ConfigLoader):
    assert loader._load_file(path_tmp / "nonexistent.toml") == {}


def test_load_file_unsupported_format(path_tmp: Path, loader: ConfigLoader):
    cfg = path_tmp / "stepup.ini"
    cfg.write_text("[stepup]\nnum_workers = 4\n")
    with pytest.raises(ValueError, match="Unsupported config file format"):
        loader._load_file(cfg)


def test_load_file_tilde_expansion(path_tmp: Path, loader: ConfigLoader, monkeypatch):
    monkeypatch.setenv("HOME", str(path_tmp))
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"num_workers = 7\n")
    assert loader._load_file("~/stepup.toml") == {"num_workers": 7}


def test_load_file_preserves_nested_sections(path_tmp: Path, loader: ConfigLoader):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"num_workers = 4\n[boot]\nclean = false\n")
    assert loader._load_file(cfg) == {"num_workers": 4, "boot": {"clean": False}}


# ---------------------------------------------------------------------------
# _configs population
# ---------------------------------------------------------------------------


def test_configs_populated_from_paths(path_tmp: Path):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"num_workers = 8\n")
    loader = ConfigLoader("stepup", config_paths=[path_tmp / "stepup.toml"], environ={})
    assert loader._configs == [(cfg, {"num_workers": 8})]


def test_configs_one_dict_per_paths(path_tmp: Path):
    a = path_tmp / "a.toml"
    a.write_bytes(b"num_workers = 4\n")
    b = path_tmp / "b.toml"
    b.write_bytes(b"num_workers = 8\n")
    loader = ConfigLoader("stepup", config_paths=[a, b], environ={})
    assert loader._configs == [(a, {"num_workers": 4}), (b, {"num_workers": 8})]


def test_configs_missing_stem_gives_empty_dict():
    cfg = "/nonexistent/stepup"
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    assert loader._configs == [(Path(cfg), {})]


def test_configs_pyproject_auto_section(path_tmp: Path):
    cfg = path_tmp / "pyproject.toml"
    cfg.write_bytes(b"[tool.stepup]\nnum_workers = 2\n[tool.stepup.boot]\nclean = false\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    assert loader._configs == [(cfg, {"num_workers": 2, "boot": {"clean": False}})]


def test_env_preloaded_at_construction():
    loader = ConfigLoader("stepup", environ={"STEPUP_NUM_WORKERS": "8", "OTHER": "x"})
    assert loader._env == {"STEPUP_NUM_WORKERS": "8", "OTHER": "x"}


# ---------------------------------------------------------------------------
# patch_parser — basic injection
# ---------------------------------------------------------------------------


def test_patch_parser_from_file(path_tmp, parser, loader):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"num_workers = 8\n")
    loader._configs = [(cfg, loader._load_file(cfg))]
    loader.patch_parser(parser)
    assert parser.parse_args([]).num_workers == 8


def test_patch_parser_later_file_wins(path_tmp, parser):
    a = path_tmp / "a.toml"
    a.write_bytes(b"num_workers = 4\n")
    b = path_tmp / "b.toml"
    b.write_bytes(b"num_workers = 8\n")
    loader = ConfigLoader("stepup", config_paths=[a, b], environ={})
    loader.patch_parser(parser)
    assert parser.parse_args([]).num_workers == 8


def test_patch_parser_cli_still_wins(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"num_workers = 8\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser)
    assert parser.parse_args(["--num-workers", "16"]).num_workers == 16


def test_patch_parser_skip_file_config(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'root = "/tmp"\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    with pytest.raises(ValueError, match="Unsupported config key"):
        loader.patch_parser(parser, skip_file_config={"root"})


def test_patch_parser_unsupported_config_key(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"unsupported_key = 42\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    with pytest.raises(ValueError, match="Unsupported config key"):
        loader.patch_parser(parser)


# ---------------------------------------------------------------------------
# patch_parser — section navigation
# ---------------------------------------------------------------------------


def test_patch_parser_section(path_tmp, plugin_parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'num_workers = 8\n[plugin]\nquality = "high"\nnum_jobs = 4\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(plugin_parser, "plugin")
    ns = plugin_parser.parse_args([])
    assert ns.quality == "high"
    assert ns.num_jobs == 4


def test_patch_parser_no_section_uses_top_level(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"num_workers = 8\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser)
    assert parser.parse_args([]).num_workers == Decimal("8")
    assert isinstance(parser.parse_args([]).num_workers, Decimal)


def test_patch_parser_dotted_section(path_tmp, plugin_parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'[plugins.my_plugin]\nquality = "draft"\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(plugin_parser, "plugins.my_plugin")
    assert plugin_parser.parse_args([]).quality == "draft"


def test_patch_parser_missing_section_leaves_defaults(path_tmp, plugin_parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"num_workers = 8\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(plugin_parser, "nonexistent")
    ns = plugin_parser.parse_args([])
    assert ns.quality is None
    assert ns.num_jobs == 1


# ---------------------------------------------------------------------------
# patch_parser — environment variable overlay
# ---------------------------------------------------------------------------


def test_patch_parser_env_basic(parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_NUM_WORKERS": "12"})
    loader.patch_parser(parser)
    assert parser.parse_args([]).num_workers == 12
    assert isinstance(parser.parse_args([]).num_workers, Decimal)


def test_patch_parser_env_bool_flag_true(parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_DEBUG": "yes"})
    loader.patch_parser(parser)
    assert parser.parse_args([]).debug is True


def test_patch_parser_env_bool_flag_false(parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_DEBUG": "0"})
    loader.patch_parser(parser)
    assert parser.parse_args([]).debug is False


def test_patch_parser_env_bool_optional_action():
    p = argparse.ArgumentParser()
    p.add_argument("--clean", action=argparse.BooleanOptionalAction, default=True)
    loader = ConfigLoader("app", environ={"APP_CLEAN": "no"})
    loader.patch_parser(p)
    assert p.parse_args([]).clean is False


def test_patch_parser_env_count_action():
    p = argparse.ArgumentParser()
    p.add_argument("--verbose", "-v", action="count", default=0)
    loader = ConfigLoader("app", environ={"APP_VERBOSE": "2"})
    loader.patch_parser(p)
    assert p.parse_args(["-v"]).verbose == 3  # 2 (injected) + 1 (from -v)


def test_patch_parser_nargs_optional_env_overrides_const():
    # Config/env set the const (value when flag is given bare), not the default.
    # The feature stays disabled until --perf is passed.
    p = argparse.ArgumentParser()
    p.add_argument("--perf", default=None, nargs="?", const="500")
    loader = ConfigLoader("app", environ={"APP_PERF": "1000"})
    loader.patch_parser(p)
    assert p.parse_args([]).perf is None  # feature still disabled by default
    assert p.parse_args(["--perf"]).perf == "1000"  # bare flag uses overridden const
    assert p.parse_args(["--perf", "2000"]).perf == "2000"  # explicit CLI value still wins


def test_patch_parser_nargs_optional_file_overrides_const(path_tmp):
    cfg = path_tmp / "app.toml"
    cfg.write_bytes(b'perf = "1000"\n')
    p = argparse.ArgumentParser()
    p.add_argument("--perf", default=None, nargs="?", const="500")
    loader = ConfigLoader("app", config_paths=[cfg], environ={})
    loader.patch_parser(p)
    assert p.parse_args([]).perf is None  # feature still disabled by default
    assert p.parse_args(["--perf"]).perf == "1000"  # bare flag uses overridden const


def test_patch_parser_env_overrides_file(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"num_workers = 2\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={"STEPUP_NUM_WORKERS": "8"})
    loader.patch_parser(parser)
    assert parser.parse_args([]).num_workers == 8
    assert isinstance(parser.parse_args([]).num_workers, Decimal)


def test_patch_parser_env_unknown_vars_ignored(parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_UNKNOWN": "x", "OTHER": "y"})
    loader.patch_parser(parser)
    assert parser.parse_args([]).num_workers == Decimal("1.2")  # unchanged default
    assert isinstance(parser.parse_args([]).num_workers, Decimal)


def test_patch_parser_env_with_section(parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_BOOT_NUM_WORKERS": "12"})
    loader.patch_parser(parser, "boot")
    assert parser.parse_args([]).num_workers == 12
    assert isinstance(parser.parse_args([]).num_workers, Decimal)


def test_patch_parser_env_section_prefix_required(parser):
    # The un-prefixed name is NOT matched when a section is given.
    loader = ConfigLoader("stepup", environ={"STEPUP_NUM_WORKERS": "12"})
    loader.patch_parser(parser, "boot")
    assert parser.parse_args([]).num_workers == Decimal("1.2")  # unchanged


def test_patch_parser_env_dotted_section(plugin_parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_PLUGINS_MY_PLUGIN_QUALITY": "high"})
    loader.patch_parser(plugin_parser, "plugins.my_plugin")
    assert plugin_parser.parse_args([]).quality == "high"


def test_patch_parser_env_hyphen_section(plugin_parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_RENDER_JINJA_QUALITY": "high"})
    loader.patch_parser(plugin_parser, "render-jinja")
    assert plugin_parser.parse_args([]).quality == "high"


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
    loader.patch_parser(parser, merge_handlers={"search_paths": lambda a, b: f"{a}:{b}"})
    assert parser.parse_args([]).search_paths == "/usr/share:/home/user/lib"


def test_patch_parser_merge_handler_two_files(path_tmp, parser):
    a = path_tmp / "a.toml"
    a.write_bytes(b'search_paths = "/usr/share"\n')
    b = path_tmp / "b.toml"
    b.write_bytes(b'search_paths = "/opt"\n')
    loader = ConfigLoader("stepup", config_paths=[a, b], environ={})
    loader.patch_parser(parser, merge_handlers={"search_paths": lambda a, b: f"{a}:{b}"})
    assert parser.parse_args([]).search_paths == "/usr/share:/opt"


def test_patch_parser_merge_handler_only_env(parser):
    loader = ConfigLoader("stepup", environ={"STEPUP_SEARCH_PATHS": "/home/user/lib"})
    loader.patch_parser(parser, merge_handlers={"search_paths": lambda a, b: f"{a}:{b}"})
    # No file value, so handler is not invoked; env value used directly.
    assert parser.parse_args([]).search_paths == "/home/user/lib"


def test_patch_parser_merge_handler_only_file(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'search_paths = "/usr/share"\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser, merge_handlers={"search_paths": lambda a, b: f"{a}:{b}"})
    # No env value, so handler is not invoked; file value used directly.
    assert parser.parse_args([]).search_paths == "/usr/share"


# ---------------------------------------------------------------------------
# patch_parser — multiple parsers
# ---------------------------------------------------------------------------


def test_patch_parser_section_isolates_parsers(path_tmp, parser, plugin_parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'num_workers = "1.1"\n[plugin]\nquality = "high"\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    loader.patch_parser(parser)
    loader.patch_parser(plugin_parser, "plugin")
    assert parser.parse_args([]).num_workers == Decimal("1.1")
    assert isinstance(parser.parse_args([]).num_workers, Decimal)
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
    loader.patch_parser(p)
    assert p.parse_args([]).mode == "slow"


def test_patch_parser_choices_invalid_from_file(path_tmp):
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["fast", "slow"], default="fast")
    cfg = path_tmp / "app.toml"
    cfg.write_bytes(b'mode = "turbo"\n')
    loader = ConfigLoader("app", config_paths=[cfg], environ={})
    with pytest.raises(ValueError, match=r"'turbo'.*mode.*in .*app\.toml"):
        loader.patch_parser(p)


def test_patch_parser_choices_valid_from_env():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["fast", "slow"], default="fast")
    loader = ConfigLoader("app", environ={"APP_MODE": "slow"})
    loader.patch_parser(p)
    assert p.parse_args([]).mode == "slow"


def test_patch_parser_choices_invalid_from_env():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["fast", "slow"], default="fast")
    loader = ConfigLoader("app", environ={"APP_MODE": "turbo"})
    with pytest.raises(ValueError, match=r"'turbo'.*mode.*from APP_MODE"):
        loader.patch_parser(p)


# ---------------------------------------------------------------------------
# _patches tracking
# ---------------------------------------------------------------------------


def test_patches_recorded_no_section(parser, loader):
    loader.patch_parser(parser)
    assert len(loader._patches) == 1
    section, actions = loader._patches[0]
    assert section is None
    assert "num_workers" in actions


def test_patches_recorded_with_section(parser, loader):
    loader.patch_parser(parser, "boot")
    section, actions = loader._patches[0]
    assert section == "boot"
    assert "num_workers" in actions


def test_patches_accumulate_across_calls(parser, plugin_parser, loader):
    loader.patch_parser(parser)
    loader.patch_parser(plugin_parser, "plugin")
    assert len(loader._patches) == 2
    assert loader._patches[0][0] is None
    assert loader._patches[1][0] == "plugin"


def test_patches_not_recorded_on_error(path_tmp, parser):
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b"unknown_key = 1\n")
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    with pytest.raises(ValueError):
        loader.patch_parser(parser)
    assert loader._patches == []


# ---------------------------------------------------------------------------
# env_to_toml_map
# ---------------------------------------------------------------------------


def test_env_to_toml_map_empty_before_patch():
    loader = ConfigLoader("stepup", environ={"STEPUP_NUM_WORKERS": "8"})
    assert loader.env_to_toml_map() == {}


def test_env_to_toml_map_no_section():
    loader = ConfigLoader("stepup", environ={"STEPUP_NUM_WORKERS": "8"})
    p = argparse.ArgumentParser()
    p.add_argument("--num-workers", dest="num_workers", type=int)
    loader.patch_parser(p)
    result = loader.env_to_toml_map()
    assert result == {"STEPUP_NUM_WORKERS": [(None, "num_workers", 8)]}


def test_env_to_toml_map_with_section():
    loader = ConfigLoader("stepup", environ={"STEPUP_BOOT_NUM_WORKERS": "8"})
    p = argparse.ArgumentParser()
    p.add_argument("--num-workers", dest="num_workers", type=int)
    loader.patch_parser(p, "boot")
    result = loader.env_to_toml_map()
    assert result == {"STEPUP_BOOT_NUM_WORKERS": [("boot", "num_workers", 8)]}


def test_env_to_toml_map_unset_var_excluded(parser, loader):
    loader.patch_parser(parser)
    assert loader.env_to_toml_map() == {}


def test_env_to_toml_map_multiple_sections():
    loader = ConfigLoader(
        "stepup",
        environ={"STEPUP_NUM_WORKERS": "4", "STEPUP_BOOT_LABEL": "prod"},
    )
    p_main = argparse.ArgumentParser()
    p_main.add_argument("--num-workers", dest="num_workers", type=int)
    p_boot = argparse.ArgumentParser()
    p_boot.add_argument("--label")
    loader.patch_parser(p_main)
    loader.patch_parser(p_boot, "boot")
    result = loader.env_to_toml_map()
    assert result == {
        "STEPUP_NUM_WORKERS": [(None, "num_workers", 4)],
        "STEPUP_BOOT_LABEL": [("boot", "label", "prod")],
    }


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


def test_full_integration(path_tmp, parser):
    etc_cfg = path_tmp / "etc.toml"
    etc_cfg.write_bytes(b'num_workers = 8\nsearch_paths = "/usr/share"\n')

    pyproject = path_tmp / "pyproject.toml"
    pyproject.write_bytes(b'[tool.stepup]\nnum_workers = 2\nlabel = "proj"\n')

    loader = ConfigLoader(
        "stepup",
        config_paths=[etc_cfg, pyproject],
        environ={"STEPUP_DEBUG": "yes", "STEPUP_SEARCH_PATHS": "/home/user/lib"},
    )
    loader.patch_parser(parser, merge_handlers={"search_paths": lambda a, b: f"{a}:{b}"})

    ns = parser.parse_args(["--label", "cli"])

    assert ns.num_workers == Decimal("2")  # pyproject overrides etc
    assert isinstance(parser.parse_args([]).num_workers, Decimal)
    assert ns.debug is True  # from env
    assert ns.search_paths == "/usr/share:/home/user/lib"  # merge handler
    assert ns.label == "cli"  # CLI overrides pyproject
