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
"""Multi-source configuration loader for StepUp and its extensions.

Config files are loaded at construction into an ordered list; the environment
is also preloaded.  No merging takes place until `patch_parser` is called.
That method applies defaults to any argparse parser one option at a time,
working through the config list in order (later files win) and overlaying
the environment last.

Environment variable naming convention
--------------------------------------

The env var name is derived from the prefix, the section (if any), and the dest:

- No section: `STEPUP_DEBUG` (prefix + dest)
- Section `"build"`: `STEPUP_BUILD_JOBS` (prefix + section + dest)
- Dotted section `"some.thing"`: `STEPUP_SOME_THING`
- Hyphenated section `"render-jinja"`: `STEPUP_RENDER_JINJA`

Dots and hyphens in section names are replaced by underscores.

```python
loader = ConfigLoader(
    "stepup",
    config_paths=[
        "/etc/stepup.toml",
        "~/.config/stepup.toml",
        "./stepup.toml",
        "pyproject.toml",
    ],
)

# Main parser — top-level config keys, env vars: STEPUP_<DEST>
loader.patch_parser(main_parser)

# Tool subparser — named section, env vars: STEPUP_MYTOOL_<DEST>
loader.patch_parser(mytool_parser, "mytool", {"paths": merge_paths})
```
"""

import argparse
import os
import tomllib
from collections.abc import Callable
from typing import Any

import attrs
from path import Path
from rich.console import Console
from rich.syntax import Syntax

from stepup.core.utils import string_to_bool

__all__ = (
    "ConfigLoader",
    "show_config_subcommand",
)


@attrs.define
class ConfigLoader:
    """Load configuration from files and environment, then patch argparse parsers.

    At construction each config stem is loaded into a separate dict stored in
    `_configs`; the environment is preloaded into `_env`.  No merging happens
    until `patch_parser` is called.

    `patch_parser` iterates through `_configs` in order, then overlays `_env`,
    setting each matching argument default one at a time.  Optional per-option
    *merge_handlers* can combine an accumulated value with the next one instead
    of replacing it outright.

    Parameters
    ----------
    prefix
        Prefix for environment variable names.
        With no section, `"stepup"` maps dest `log_level` to `STEPUP_LOG_LEVEL`.
        With section `"build"`, dest `jobs` maps to `STEPUP_BUILD_JOBS`.
        Also determines the default *pyproject_section* (e.g. `"tool.stepup"`).
    config_paths
        Ordered list of config file locations, from lowest to highest priority.
        The special filename `pyproject.toml` is loaded from the section derived from `prefix`.
    environ
        Environment dict to read from.
        `None` (default) snapshots `os.environ` at construction time.
        Pass an explicit dict (including `{}`) in tests to avoid depending on the real environment.
    """

    _prefix: str = attrs.field()
    _config_paths: list[str] = attrs.field(factory=list, kw_only=True)
    _environ: dict[str, str] | None = attrs.field(default=None)
    _configs: list[dict] = attrs.field(init=False, factory=list)
    _env: dict[str, str] = attrs.field(init=False, factory=dict)
    _patches: list[tuple[str | None, dict[str, argparse.Action]]] = attrs.field(
        init=False, factory=list
    )

    def __attrs_post_init__(self) -> None:
        self._configs = [(Path(path), self._load_file(path)) for path in self._config_paths]
        self._env = dict(os.environ) if self._environ is None else self._environ

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_file(self, path: str, section: str | None = None) -> dict:
        """Load a TOML config file and navigate to a section, without filtering.

        Parameters
        ----------
        path
            Path to the config file.
            Returns an empty dict if the file does not exist.
        section
            Dotted key path to navigate before returning, e.g. `"tool.stepup"`.
            Returns an empty dict if the path is absent or leads to a non-dict value.

        Returns
        -------
        data
            Full dict at the requested section level, with no parser-key filtering.
        """
        path = Path(path).expanduser()
        if not path.is_file():
            return {}
        if path.suffix.lower() != ".toml":
            raise ValueError(f"Unsupported config file format: {path.suffix!r}")
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        if path.name == "pyproject.toml":
            data = data.get("tool", {}).get(self._prefix.lower(), {})

        if section:
            for part in section.split("."):
                if not isinstance(data, dict):
                    return {}
                data = data.get(part, {})

        return data if isinstance(data, dict) else {}

    def _env_key(self, section: str | None, dest: str) -> str:
        """Compute the environment variable name for a (section, dest) pair.

        Dots and hyphens in the section name are converted to underscores.
        """
        section_str = (section.upper().replace(".", "_").replace("-", "_") + "_") if section else ""
        return f"{self._prefix.upper()}_{section_str}{dest.upper()}"

    def _actions(self, parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
        """Collect all user-facing argument actions from *parser*, keyed by dest.

        Parameters
        ----------
        parser
            The parser to inspect.

        Returns
        -------
        actions
            Dict mapping each dest to its action, excluding the built-in
            `help` action and subparser actions.
        """
        return {
            a.dest: a
            for a in parser._actions
            if a.dest != "help" and not isinstance(a, argparse._SubParsersAction)
        }

    def _coerce_type(self, raw: Any, action: argparse.Action) -> Any:
        """Parse a raw config value into the target Python type and validate choices.

        Falls back to `string_to_bool` for boolean flags (`store_true`, `store_false`,
        `BooleanOptionalAction`), `int` for count actions, or the action's `type` callable.
        After coercion, validates against `action.choices` when present.

        Parameters
        ----------
        raw
            The raw value from the environment variable (always a string)
            or from a TOML config file (any TOML type).
        action
            The argparse action for this dest.

        Returns
        -------
        value
            The parsed value in the appropriate Python type.

        Raises
        ------
        ValueError
            When the coerced value is not in `action.choices`.
        """
        if isinstance(
            action,
            (
                argparse._StoreTrueAction,
                argparse._StoreFalseAction,
                argparse.BooleanOptionalAction,
            ),
        ):
            value = string_to_bool(raw)
        elif isinstance(action, argparse._CountAction):
            value = int(raw)
        elif action.type is not None:
            value = action.type(raw)
        else:
            value = raw
        if action.choices is not None and value not in action.choices:
            raise ValueError(
                f"Invalid value {value!r} for {action.dest!r}: choose from {list(action.choices)}"
            )
        return value

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def prefix(self) -> str:
        """Env var prefix for this loader."""
        return self._prefix

    @property
    def config_paths(self) -> list[str]:
        """Config file paths, in priority order (lowest to highest)."""
        return list(self._config_paths)

    def relevant_env_vars(self) -> dict[str, str]:
        """Return env vars whose names start with the uppercase prefix followed by `_`."""
        key_prefix = self._prefix.upper() + "_"
        return {k: v for k, v in self._env.items() if k.startswith(key_prefix)}

    def dump_with_provenance(self) -> dict[str, dict[str, tuple[Any, str]]]:
        """Return the merged config with the source of each value.

        Iterates over loaded config files in priority order (later files override earlier
        ones for the same key).
        Only flat (non-dict) values are included; nested subsections beyond one level are
        ignored, which matches the behavior of `patch_parser`.

        Returns
        -------
        provenance_map
            Dict mapping section names to `{key: (value, source)}` dicts.
            The empty string `""` is used as the section name for top-level keys.
            *source* is the string representation of the config file path.
        """
        result: dict[str, dict[str, tuple[Any, str]]] = {}

        for path, config in self._configs:
            path_str = str(path)
            for key, value in config.items():
                if isinstance(value, dict):
                    if key not in result:
                        result[key] = {}
                    for sub_key, sub_value in value.items():
                        if not isinstance(sub_value, dict):
                            result[key][sub_key] = (sub_value, path_str)
                else:
                    if "" not in result:
                        result[""] = {}
                    result[""][key] = (value, path_str)

        return result

    def env_to_toml_map(self) -> dict[str, list[tuple[str | None, str, Any]]]:
        """Map active env vars to their TOML location across all registered parsers.

        Iterates over every `(section, dest)` pair recorded by prior `patch_parser` calls.
        For each pair whose env var is present in the environment, the coerced value is
        collected.
        Call this only after all `patch_parser` calls have been made.

        Returns
        -------
        mapping
            Dict keyed by env var name.
            Each value is a list of `(section, dest, coerced_value)` tuples — one per
            patched parser that registered the dest.
            Only env vars that are actually set in the environment are included.
        """
        result: dict[str, list[tuple[str | None, str, Any]]] = {}
        seen: set[tuple[str | None, str]] = set()
        for section, actions in self._patches:
            for dest, action in actions.items():
                if (section, dest) in seen:
                    continue
                seen.add((section, dest))
                env_key = self._env_key(section, dest)
                env_value = self._env.get(env_key)
                if env_value is not None:
                    try:
                        coerced = self._coerce_type(env_value, action)
                    except ValueError:
                        continue
                    result.setdefault(env_key, []).append((section, dest, coerced))
        return result

    def patch_parser(
        self,
        parser: argparse.ArgumentParser,
        section: str | None = None,
        merge_handlers: dict[str, Callable[[Any, Any], Any]] | None = None,
        skip_file_config: set[str] | None = None,
    ) -> None:
        """Inject config defaults and env-var overrides into an argparse parser.

        For each argument in *parser*, values are accumulated from `_configs`
        in order (later files win) and then from `_env`.  When a
        *merge_handler* is registered for a dest and both an accumulated value
        and an incoming value are non-`None`, the handler is called instead
        of the plain "incoming replaces accumulated" rule.

        Parameters
        ----------
        parser
            Argparse parser to patch.
            Argument defaults are mutated in place.
        section
            Dotted key path into each config dict, e.g. `"build"` or `"some.other"`.
            `None` uses the top-level dict.
            Configs that do not contain the section are silently skipped.
        merge_handlers
            Per-dest callables `fn(accumulated, incoming) -> merged` called
            when both an accumulated value and a new value are available.
            Without a handler the incoming value replaces the accumulated one.
        skip_file_config
            Dest names to skip when applying defaults read from files.
            Useful for arguments that are set explicitly on the command line or by other means.
        """
        handlers = merge_handlers or {}
        section_parts = section.split(".") if section else []

        def get_location_error_msg(path, loc):
            if loc == "":
                return f"Config file {path} did not load to a dict"
            return f"Config file {path} did not load to a dict at section {loc!r}"

        # Navigate each config to the requested section up front.
        config_views: list[dict] = []
        for path, config in self._configs:
            data = config
            loc = f"tool.{self._prefix.lower()}" if path.name == "pyproject.toml" else ""
            if not isinstance(data, dict):
                raise TypeError(get_location_error_msg(path, loc))
            for part in section_parts:
                data = data.get(part, {})
                if not isinstance(data, dict):
                    raise TypeError(get_location_error_msg(path, loc))
                loc += f".{part}" if loc else part
            data = {k: v for k, v in data.items() if not isinstance(v, dict)}
            config_views.append((path, data))

        skip_file_config = skip_file_config or set()
        action_map = self._actions(parser)
        for dest, action in action_map.items():
            value = None

            # Apply file configs in priority order.
            # Keys in skip_file_config are intentionally not popped, so they
            # remain in data and get flagged as unsupported by the check below.
            if dest not in skip_file_config:
                for path, data in config_views:
                    incoming = data.pop(dest, None)
                    if incoming is not None:
                        try:
                            incoming = self._coerce_type(incoming, action)
                        except (ValueError, TypeError) as exc:
                            raise type(exc)(f"{exc} (in {path})") from exc
                        handler = None if value is None else handlers.get(dest)
                        value = incoming if handler is None else handler(value, incoming)

            # Overlay environment variable (highest priority).
            env_key = self._env_key(section, dest)
            env_value = self._env.get(env_key)
            if env_value is not None:
                try:
                    incoming = self._coerce_type(env_value, action)
                except (ValueError, TypeError) as exc:
                    raise type(exc)(f"{exc} (from {env_key})") from exc
                handler = None if value is None else handlers.get(dest)
                value = incoming if handler is None else handler(value, incoming)

            if value is not None:
                if action.nargs == "?":
                    action.const = value
                else:
                    action.default = value

        # Detect any unsupported keys in the config files and raise an error.
        for path, data in config_views:
            if len(data) > 0:
                msg = f"Unsupported config key(s) in {path}: {list(data.keys())}"
                if section:
                    msg += f" (section {section!r})"
                raise ValueError(msg)

        self._patches.append((section, action_map))


def show_config_subcommand(subparsers, loader: ConfigLoader) -> Callable:
    """Define command-line arguments for the show-config tool.

    Parameters
    ----------
    subparsers
        The sub parser to add the show-config tool to.
    loader
        The configuration loader used to read and merge configuration sources.
    """
    subparsers.add_parser(
        "show-config",
        help="Print the effective StepUp configuration as TOML.",
    )

    def show_config_tool(args: argparse.Namespace):
        _render_config(loader)

    return show_config_tool


def _toml_value(value: Any) -> str:
    """Format a Python value as a TOML literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return f'"{value!s}"'


def _render_config(loader: ConfigLoader) -> None:
    lines: list[str] = []

    env_prefix = loader.prefix.upper() + "_"
    stepup_root = Path(loader.config_paths[-1]).parent
    short_paths = {}

    lines.append("# Config files (lowest to highest priority):")
    for path in loader.config_paths:
        short = path
        if short.startswith(stepup_root):
            short = short.relpath()
            if not short.startswith("."):
                short = "." / short
        short = Path(short)
        tag = "FOUND:  " if short.is_file() else "MISSING:"
        lines.append(f"#   {tag} {short}")
        short_paths[path] = short
    lines.append(f"# Environment variables: {env_prefix}*")

    provenance = loader.dump_with_provenance()
    env_map = loader.env_to_toml_map()
    all_env_vars = loader.relevant_env_vars()

    # Overlay env var values onto the provenance dict (env vars win, sourced as "$VAR").
    matched_env_keys: set[str] = set()
    merged: dict[str, dict[str, tuple[Any, str]]] = {k: dict(v) for k, v in provenance.items()}
    for env_key, matches in env_map.items():
        matched_env_keys.add(env_key)
        for section, dest, coerced in matches:
            section_key = section or ""
            if section_key not in merged:
                merged[section_key] = {}
            merged[section_key][dest] = (coerced, f"${env_key}")

    top = merged.get("", {})
    sections = {k: v for k, v in merged.items() if k and v}
    unmatched_env_vars = {k: v for k, v in all_env_vars.items() if k not in matched_env_keys}

    if not top and not sections and not unmatched_env_vars:
        lines.append("")
        lines.append("# No configuration found.")
    else:
        if top:
            lines.append("")
            for key in sorted(top):
                value, source = top[key]
                short = short_paths.get(source, source)
                lines.append(f"{key} = {_toml_value(value)}  # {short}")

        for section in sorted(sections):
            lines.append("")
            lines.append(f"[{section}]")
            for key in sorted(sections[section]):
                value, source = sections[section][key]
                short = short_paths.get(source, source)
                lines.append(f"{key} = {_toml_value(value)}  # {short}")

        if unmatched_env_vars:
            lines.append("")
            lines.append(f"# Active environment variables ({env_prefix}*):")
            lines.extend(
                f"# {k} = {_toml_value(unmatched_env_vars[k])}" for k in sorted(unmatched_env_vars)
            )

    toml_text = "\n".join(lines) + "\n"
    console = Console()
    console.print(Syntax(toml_text, "toml", theme="ansi_dark", word_wrap=False))
