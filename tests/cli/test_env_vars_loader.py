# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.cli.env_vars_loader.

The entry points under test point to the fake registries in this module,
which `add_env_var_entry_points` adds to those that are installed.
"""

import os

import pytest
from core_common import add_env_var_entry_points

from stepup.core.__main__ import _setup_cli
from stepup.core.cli.env_vars_loader import EnvVarCatalog, core_env_vars, load_env_var_catalog
from stepup.core.env_vars import EnvVarInfo, EnvVarRegistry
from stepup.core.path import Path

#
# Fake registries
#


def _parse_token(value: str) -> str:
    if not value.startswith("tok-"):
        raise ValueError(f"not a token: {value!r}")
    return value


def _parse_url(value: str) -> str:
    if not value.startswith("https://"):
        raise ValueError(f"not an https URL: {value!r}")
    return value


def _crash(value: str) -> str:
    raise TypeError(f"crashed on {value!r}")


def _reject_silently(value: str) -> str:
    raise ValueError


def data_env_vars() -> EnvVarRegistry:
    return EnvVarRegistry(
        "StepUp Data",
        {
            "STEPUP_DATA_TOKEN": EnvVarInfo(secret=True, validator=_parse_token),
            "STEPUP_DATA_URL": EnvVarInfo(secret=False, validator=_parse_url),
            "STEPUP_DATA_SILENT": EnvVarInfo(secret=False, validator=_reject_silently),
            "STEPUP_DATA_INTERNAL": EnvVarInfo(secret=False, overridden=True),
        },
    )


def crash_env_vars() -> EnvVarRegistry:
    return EnvVarRegistry(
        "StepUp Crash",
        {
            "STEPUP_CRASH_PLAIN": EnvVarInfo(secret=False, validator=_crash),
            "STEPUP_CRASH_SECRET": EnvVarInfo(secret=True, validator=_crash),
        },
    )


def raising_env_vars() -> EnvVarRegistry:
    raise RuntimeError("cannot describe the variables")


def dict_env_vars() -> dict:
    return {"STEPUP_DICT_X": EnvVarInfo(secret=False)}


def bad_info_env_vars() -> EnvVarRegistry:
    return EnvVarRegistry("StepUp Bad", {"STEPUP_BAD_X": "secret"})


def bad_mapping_env_vars() -> EnvVarRegistry:
    return EnvVarRegistry("StepUp Bad", ["STEPUP_BAD_X"])


def nameless_env_vars() -> EnvVarRegistry:
    return EnvVarRegistry("", {})


def misnamed_env_vars() -> EnvVarRegistry:
    return EnvVarRegistry("StepUp Misnamed", {"STEPUP_OTHER_X": EnvVarInfo(secret=False)})


def taken_env_vars() -> EnvVarRegistry:
    return EnvVarRegistry(
        "StepUp Taken",
        {
            "STEPUP_DATA_URL": EnvVarInfo(secret=False),
            "STEPUP_BUILD_JOBS": EnvVarInfo(secret=False),
            "STEPUP_DEBUG": EnvVarInfo(secret=False),
        },
    )


def overridden_env_vars() -> EnvVarRegistry:
    return EnvVarRegistry(
        "StepUp Overridden",
        {"STEPUP_OVERRIDDEN_X": EnvVarInfo(secret=False, overridden=True, validator=int)},
    )


def build_env_vars() -> EnvVarRegistry:
    return EnvVarRegistry("StepUp Build", {"STEPUP_BUILD_EXTRA": EnvVarInfo(secret=False)})


def other_package_env_vars() -> EnvVarRegistry:
    return EnvVarRegistry("Another Package", {"STEPUP_ANOTHER_X": EnvVarInfo(secret=False)})


def _ep(func) -> str:
    """The value of an entry point that points to a fake registry of this module."""
    return f"{__name__}:{func.__name__}"


#
# Helpers
#


def _load(
    monkeypatch: pytest.MonkeyPatch,
    path_tmp: Path,
    extra: dict[str, str],
    env: dict[str, str] | None = None,
) -> EnvVarCatalog:
    """Load the catalog with extra entry points, in an environment with only the given variables.

    The caller must use the `clean_env` fixture.
    """
    monkeypatch.setenv("STEPUP_ROOT", path_tmp)
    for name, value in (env or {}).items():
        monkeypatch.setenv(name, value)
    add_env_var_entry_points(monkeypatch, extra)
    _, loader = _setup_cli()
    return load_env_var_catalog(loader)


def _messages(catalog: EnvVarCatalog) -> list[str]:
    return [problem.message for problem in catalog.problems]


#
# Tests
#


def test_core_only(monkeypatch, path_tmp, clean_env):
    catalog = _load(monkeypatch, path_tmp, {})
    assert catalog.problems == []
    assert catalog.packages == ["StepUp Core"]
    assert catalog.env_vars.keys() == core_env_vars().env_vars.keys()
    assert {entry.package for entry in catalog.env_vars.values()} == {"StepUp Core"}
    assert {entry.entry_point for entry in catalog.env_vars.values()} == {"core"}


def test_core_registry_is_valid():
    """The registry of StepUp Core passes the checks it imposes on extensions."""
    for name, info in core_env_vars().env_vars.items():
        assert name.startswith("STEPUP_")
        assert not info.secret
        assert not (info.overridden and info.validator is not None)


def test_packages_core_first(monkeypatch, path_tmp, clean_env):
    catalog = _load(
        monkeypatch,
        path_tmp,
        {"data": _ep(data_env_vars), "another": _ep(other_package_env_vars)},
    )
    assert catalog.problems == []
    assert catalog.packages == ["StepUp Core", "Another Package", "StepUp Data"]
    entry = catalog.env_vars["STEPUP_DATA_TOKEN"]
    assert entry.package == "StepUp Data"
    assert entry.entry_point == "data"
    assert entry.info.secret


def test_valid_values(monkeypatch, path_tmp, clean_env):
    env = {
        "STEPUP_DATA_TOKEN": "tok-123",
        "STEPUP_DATA_URL": "https://example.com",
        "STEPUP_DATA_INTERNAL": "anything",
    }
    catalog = _load(monkeypatch, path_tmp, {"data": _ep(data_env_vars)}, env)
    assert catalog.problems == []


def test_rejected_values(monkeypatch, path_tmp, clean_env):
    env = {
        "STEPUP_DATA_TOKEN": "hunter2",
        "STEPUP_DATA_URL": "ftp://example.com",
        "STEPUP_DATA_SILENT": "x",
    }
    catalog = _load(monkeypatch, path_tmp, {"data": _ep(data_env_vars)}, env)
    assert _messages(catalog) == [
        "$STEPUP_DATA_SILENT: invalid value",
        # The value of a secret variable is never shown, not even in the message of a validator.
        "$STEPUP_DATA_TOKEN: invalid value",
        "$STEPUP_DATA_URL: not an https URL: 'ftp://example.com'",
    ]


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("STEPUP_MAX_OUTPUT_SIZE", "lots", "invalid literal for int() with base 10: 'lots'"),
        ("STEPUP_PATH_FILTER", "+a:b", "Invalid filter item: b"),
        ("STEPUP_RPC_TRANSPORT", "udp", "must be "),
        ("STEPUP_SYNC_RPC_TIMEOUT", "never", "could not convert string to float: 'never'"),
        ("STEPUP_WATCHER", "poll", "must be "),
        ("STEPUP_WATCHER_RECURSIVE", "yes", "must be 0 or 1, got 'yes'"),
    ],
)
def test_core_rejected_values(monkeypatch, path_tmp, clean_env, name, value, message):
    if name == "STEPUP_PATH_FILTER":
        value = value.replace(":", os.pathsep)
    catalog = _load(monkeypatch, path_tmp, {}, {name: value})
    (problem,) = catalog.problems
    assert problem.location.env_var == name
    assert problem.detail.startswith(message)


def test_core_rejects_debug():
    """`STEPUP_DEBUG` is tested apart, because a bad value already stops `_setup_cli`."""
    validator = core_env_vars().env_vars["STEPUP_DEBUG"].validator
    with pytest.raises(ValueError, match="Cannot interpret 'maybe' as a boolean value"):
        validator("maybe")


def test_validator_bug(monkeypatch, path_tmp, clean_env):
    env = {"STEPUP_CRASH_PLAIN": "abc", "STEPUP_CRASH_SECRET": "hunter2"}
    catalog = _load(monkeypatch, path_tmp, {"crash": _ep(crash_env_vars)}, env)
    owner = "StepUp Crash (entry point 'crash')"
    assert _messages(catalog) == [
        f"$STEPUP_CRASH_PLAIN: bug in {owner}, whose validator raised TypeError: crashed on 'abc'",
        f"$STEPUP_CRASH_SECRET: bug in {owner}, whose validator raised TypeError",
    ]


def test_validators_of_unset_variables_are_not_called(monkeypatch, path_tmp, clean_env):
    catalog = _load(monkeypatch, path_tmp, {"crash": _ep(crash_env_vars)})
    assert catalog.problems == []


def test_import_error(monkeypatch, path_tmp, clean_env):
    catalog = _load(monkeypatch, path_tmp, {"gone": "no_such_module_for_stepup:env_vars"})
    (message,) = _messages(catalog)
    assert message.startswith(
        "entry point 'gone = no_such_module_for_stepup:env_vars': "
        "cannot be loaded: ModuleNotFoundError: "
    )
    assert catalog.packages == ["StepUp Core"]


def test_callable_raises(monkeypatch, path_tmp, clean_env):
    value = _ep(raising_env_vars)
    catalog = _load(monkeypatch, path_tmp, {"raising": value})
    assert _messages(catalog) == [
        f"entry point 'raising = {value}': raised RuntimeError: cannot describe the variables"
    ]


@pytest.mark.parametrize(
    ("func", "detail"),
    [
        (dict_env_vars, "returned dict instead of EnvVarRegistry"),
        (nameless_env_vars, "returned an EnvVarRegistry whose package is not a non-empty str"),
        (
            bad_info_env_vars,
            "returned an EnvVarRegistry whose env_vars is not a dict[str, EnvVarInfo]",
        ),
        (
            bad_mapping_env_vars,
            "returned an EnvVarRegistry whose env_vars is not a dict[str, EnvVarInfo]",
        ),
    ],
)
def test_not_a_registry(monkeypatch, path_tmp, clean_env, func, detail):
    catalog = _load(monkeypatch, path_tmp, {"bad": _ep(func)})
    assert _messages(catalog) == [f"entry point 'bad = {_ep(func)}': {detail}"]
    assert catalog.packages == ["StepUp Core"]
    assert {entry.entry_point for entry in catalog.env_vars.values()} == {"core"}


def test_misnamed(monkeypatch, path_tmp, clean_env):
    catalog = _load(monkeypatch, path_tmp, {"misnamed": _ep(misnamed_env_vars)})
    assert _messages(catalog) == [
        "$STEPUP_OTHER_X: registered by StepUp Misnamed (entry point 'misnamed'), "
        "but not prefixed with STEPUP_MISNAMED_"
    ]
    # The variable is still registered, because the package does read it.
    assert catalog.env_vars["STEPUP_OTHER_X"].package == "StepUp Misnamed"


def test_reserved_entry_point_name(monkeypatch, path_tmp, clean_env):
    value = _ep(build_env_vars)
    catalog = _load(monkeypatch, path_tmp, {"build": value})
    assert _messages(catalog) == [
        f"entry point 'build = {value}': the name 'build' is taken by the subcommand 'stepup build'"
    ]


def test_registered_twice_or_setting(monkeypatch, path_tmp, clean_env):
    catalog = _load(
        monkeypatch,
        path_tmp,
        {"data": _ep(data_env_vars), "taken": _ep(taken_env_vars)},
    )
    owner = "StepUp Taken (entry point 'taken')"
    messages = _messages(catalog)
    assert f"$STEPUP_DEBUG: registered by StepUp Core (entry point 'core') and by {owner}" in (
        messages
    )
    assert f"$STEPUP_DATA_URL: registered by StepUp Data (entry point 'data') and by {owner}" in (
        messages
    )
    assert f"$STEPUP_BUILD_JOBS: registered by {owner}, but it configures a setting" in messages
    # The first registration is kept, and a setting is never registered.
    assert catalog.env_vars["STEPUP_DEBUG"].package == "StepUp Core"
    assert catalog.env_vars["STEPUP_DATA_URL"].package == "StepUp Data"
    assert "STEPUP_BUILD_JOBS" not in catalog.env_vars


def test_overridden_with_validator(monkeypatch, path_tmp, clean_env):
    catalog = _load(
        monkeypatch,
        path_tmp,
        {"overridden": _ep(overridden_env_vars)},
        {"STEPUP_OVERRIDDEN_X": "not an int"},
    )
    # The validator is not called on the value, because the variable is overridden anyway.
    assert _messages(catalog) == [
        "$STEPUP_OVERRIDDEN_X: registered by StepUp Overridden (entry point 'overridden') "
        "as overridden by StepUp, which leaves its validator unused"
    ]
