# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Multi-source configuration loader for StepUp and its extensions.

Config files are loaded at construction into an ordered list;
the environment is also preloaded.
No merging takes place until `patch_parser` is called.
That method applies defaults to any argparse parser one option at a time,
working through the config list in order (later files win)
and overlaying the environment last.

## Environment Variable Naming Convention

The section of a subparser is the last word of its `prog`
(argparse prefixes it with the parent's `prog`, e.g. `"stepup build"` yields `"build"`).
The env var name is derived from the prefix, the section (if any), and the `dest`:

- No section: `STEPUP_<DEST>` (prefix + dest)
- Section `"build"`: `STEPUP_BUILD_JOBS` (prefix + section + dest)
- Dotted section `"some.thing"`: `STEPUP_SOME_THING_<DEST>`
- Hyphenated section `"render-jinja"`: `STEPUP_RENDER_JINJA_<DEST>`

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
loader.patch_parser(main_parser, use_section=False)

# Tool subparser — section taken from the last word of its prog ("mytool"),
# env vars: STEPUP_MYTOOL_<DEST>
loader.patch_parser(mytool_parser, merge_handlers={"paths": merge_paths})

# Once every parser is patched: report everything that is wrong with the configuration.
messages = loader.check()
```

## Error Handling

Loading and patching never raise on a bad configuration:
problems are recorded and returned together by `problems` (or `check`, for the messages alone),
so that a user gets the complete list in one go instead of one problem per run.
Recognizing an unsupported key or an unknown section also requires
every parser to have been patched first.
"""

import argparse
import difflib
import os
import sys
import tomllib
from collections.abc import Callable
from typing import Any

import attrs
from path import Path
from rich.console import Console
from rich.syntax import Syntax
from rich.text import Text

from stepup.core.enums import ReturnCode
from stepup.core.exceptions import ConfigError
from stepup.core.tool import ERROR_STYLE, ToolFunc, print_error
from stepup.core.utils import string_to_bool

__all__ = (
    "ConfigLoader",
    "ConfigProblem",
    "config_subcommand",
    "format_config_problems",
    "print_config_problems",
)


_CONVERSION_ERRORS = (ValueError, TypeError, ArithmeticError, argparse.ArgumentTypeError)
"""Exceptions with which a conversion may reject a config value.

`ArithmeticError` covers `decimal.InvalidOperation`, raised by a `Decimal` option.
Any other exception points at a bug in the conversion function instead of at the value,
and keeps its traceback.
"""


def _conversion_detail(exc: Exception) -> str:
    """Describe an exception raised while converting a config value, for an error message.

    A conversion is expected to signal a rejected value with a `ValueError` or a `TypeError`,
    whose message is self-explanatory.
    Any other exception is named, because its message alone rarely is,
    e.g. `decimal.InvalidOperation` for a `Decimal` option.
    """
    if isinstance(exc, (ValueError, TypeError)):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def _short_path(path: str | Path) -> Path:
    """Shorten a config file path for display in a message.

    A path inside the working directory is made relative to it, with a `./` prefix,
    which is how `stepup config` lists the config files.
    Any other path is shown as it is:
    an absolute path is easier to read than a relative one climbing out of the tree,
    and a path like `~/.config/stepup.toml` already says where it is.
    """
    path = Path(path)
    if not path.startswith(os.getcwd() + os.sep):
        return path
    short = path.relpath()
    return short if short.startswith(".") else "./" / short


def _not_a_section(section_path: str) -> str:
    """Report a section that holds a plain value instead of a table with settings."""
    return f"'{section_path}' is configured as a value, but a section [{section_path}] is expected"


def _not_a_setting(section_path: str) -> str:
    """Report a setting that holds a table with settings instead of a plain value.

    The returned message is a fragment,
    to be completed with the location phrase of the section the setting belongs to.
    """
    return f"'{section_path}' is configured as a section, but a value is expected"


def _hint(candidates: list[str]) -> str:
    """Turn close matches of a mistyped name into a suffix for an error message.

    Returns an empty string when there are no candidates,
    so the suffix can be appended unconditionally.
    """
    if len(candidates) == 0:
        return ""
    return " (did you mean " + " or ".join(repr(candidate) for candidate in candidates) + "?)"


@attrs.define(frozen=True)
class ConfigProblem:
    """Something wrong with the configuration, and where it was found.

    The location is what `config` needs to display a problem
    on the line of the setting, section or config file it concerns.
    """

    detail: str = attrs.field()
    """What is wrong, without naming the config file or environment variable."""

    path: Path | None = attrs.field(default=None)
    """The config file in which the problem was found, `None` for an environment variable."""

    section: str | None = attrs.field(default=None)
    """The section in which the problem was found, `None` for the top level of the file."""

    key: str | None = attrs.field(default=None)
    """The setting the problem concerns, `None` when the whole section or file is at fault."""

    env_var: str | None = attrs.field(default=None)
    """The environment variable in which the problem was found, `None` for a config file."""

    @property
    def location(self) -> str:
        """The config file or environment variable to fix."""
        return f"${self.env_var}" if self.env_var is not None else str(_short_path(self.path))

    @property
    def message(self) -> str:
        """The problem on a single line, location included."""
        return f"{self.location}: {self.detail}"


@attrs.define
class ConfigLoader:
    """Load configuration from files and environment, then patch argparse parsers.

    At construction each config file is loaded into a separate dict stored in `_configs`;
    the environment is preloaded into `_env`.
    No merging happens until `patch_parser` is called.

    `patch_parser` iterates through `_configs` in order, then overlays `_env`,
    setting each matching argument default one at a time.
    Optional per-option `merge_handlers` can combine an accumulated value
    with the next one instead of replacing it outright.

    Anything wrong with the configuration is recorded instead of raised,
    and `problems` returns the complete list.

    Parameters
    ----------
    prefix
        Prefix for environment variable names.
        With no section, `"stepup"` maps a dest to `STEPUP_<DEST>`.
        With section `"build"`, dest `jobs` maps to `STEPUP_BUILD_JOBS`.
        Also determines the section read from `pyproject.toml` (e.g. `tool.stepup`).
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
    _configs: list[tuple[Path, dict]] = attrs.field(init=False, factory=list)
    _env: dict[str, str] = attrs.field(init=False, factory=dict)
    _patches: list[tuple[str | None, dict[str, argparse.Action]]] = attrs.field(
        init=False, factory=list
    )
    _positionals: dict[str | None, set[str]] = attrs.field(init=False, factory=dict)
    _errors: list[ConfigProblem] = attrs.field(init=False, factory=list)
    _unknown_keys: list[tuple[Path, str | None, str]] = attrs.field(init=False, factory=list)

    def __attrs_post_init__(self) -> None:
        for path in self._config_paths:
            try:
                data = self._load_file(path)
            except ConfigError as exc:
                self._errors.append(ConfigProblem(str(exc), path=Path(path)))
                data = {}
            self._configs.append((Path(path), data))
        self._env = dict(os.environ) if self._environ is None else self._environ

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_file(self, config_path: str) -> dict:
        """Load a TOML config file, without filtering.

        Parameters
        ----------
        config_path
            Path to the config file.

        Returns
        -------
        data
            Full dict of the config file, with no parser-key filtering.
            For `pyproject.toml`, the dict of the section derived from the prefix,
            e.g. `tool.stepup`.
            Empty when the file does not exist.

        Raises
        ------
        ConfigError
            When the file cannot be read, parsed, or navigated to the expected section.
            The message says what is wrong without naming the file.
        """
        path = Path(config_path).expanduser()
        if not path.is_file():
            return {}
        if path.suffix.lower() != ".toml":
            raise ConfigError("unsupported config file format, expected '.toml'")
        try:
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML syntax: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"cannot be read: {exc}") from exc

        if path.name == "pyproject.toml":
            parts = ["tool", self._prefix.lower()]
            for i, part in enumerate(parts):
                data = data.get(part, {})
                if not isinstance(data, dict):
                    raise ConfigError(_not_a_section(".".join(parts[: i + 1])))
        return data

    def _section_path(self, path: Path, section: str | None) -> str:
        """Return the dotted TOML path of `section` in the config file at `path`.

        The path is empty for the top level of a regular config file,
        because the settings there are not nested in any table.
        """
        parts = []
        if path.name == "pyproject.toml":
            parts.append(f"tool.{self._prefix.lower()}")
        if section is not None:
            parts.append(section)
        return ".".join(parts)

    def _location_phrase(self, path: Path, section: str | None) -> str:
        """Name the place in a config file that a key belongs to, preposition included."""
        section_path = self._section_path(path, section)
        return f"in section [{section_path}]" if section_path else "at the top level"

    def _env_key(self, section: str | None, dest: str) -> str:
        """Compute the environment variable name for a (section, dest) pair.

        Dots and hyphens in the section name are converted to underscores.
        """
        section_str = (section.upper().replace(".", "_").replace("-", "_") + "_") if section else ""
        return f"{self._prefix.upper()}_{section_str}{dest.upper()}"

    @staticmethod
    def _section(parser: argparse.ArgumentParser) -> str:
        """Derive the config section name from a parser's `prog`.

        Argparse prefixes the `prog` of a subparser with that of its parent,
        e.g. `"stepup clean"` for the `clean` subcommand.
        Only the last word is the section name,
        so that `stepup clean` reads `[clean]` and not `[stepup clean]`.
        """
        return parser.prog.rsplit(" ", 1)[-1]

    def _actions(
        self, parser: argparse.ArgumentParser
    ) -> tuple[dict[str, argparse.Action], set[str]]:
        """Split the user-facing arguments of `parser` into configurable and CLI-only ones.

        Parameters
        ----------
        parser
            The parser to inspect.

        Returns
        -------
        actions
            Dict mapping each dest to its action, for the arguments that a config file
            or environment variable can set.
            The built-in `help` action and subparser actions are left out.
        positional_dests
            The dests of the positional arguments, which have no `option_strings`
            and can only be given on the command line.
        """
        actions = {}
        positional_dests = set()
        for action in parser._actions:
            if action.dest == "help" or isinstance(action, argparse._SubParsersAction):
                continue
            if action.option_strings:
                actions[action.dest] = action
            else:
                positional_dests.add(action.dest)
        return actions, positional_dests

    def _coerce_type(self, raw: Any, action: argparse.Action) -> Any:
        """Parse a raw config value into the target Python type and validate choices.

        Boolean flags (`store_true`, `store_false`, `BooleanOptionalAction`) are converted
        with `string_to_bool` and count actions with `int`.
        Any other option is converted with the action's `type` callable when it has one,
        and taken as it is otherwise.
        After coercion, the value is validated against `action.choices` when present.

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

    def known_env_vars(self) -> set[str]:
        """Return the names of the environment variables that the patched parsers recognize.

        Call this only after all `patch_parser` calls have been made.

        Returns
        -------
        env_vars
            One name per `(section, dest)` pair, whether or not the variable is set.
            A variable outside this set is not necessarily a mistake:
            the prefix is shared with the variables that configure internals,
            which no parser defines an argument for.
        """
        return {
            self._env_key(section, dest) for section, actions in self._patches for dest in actions
        }

    def dump_with_provenance(self) -> dict[str, dict[str, tuple[Any, str]]]:
        """Return the merged config with the source of each value.

        Iterates over loaded config files in priority order
        (later files override earlier ones for the same key).
        Only flat (non-dict) values are included;
        nested subsections beyond one level are ignored,
        which matches the behavior of `patch_parser`.

        Returns
        -------
        provenance_map
            Dict mapping section names to `{key: (value, source)}` dicts.
            The empty string `""` is used as the section name for top-level keys.
            `source` is the string representation of the config file path.
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
        For each pair whose env var is present in the environment, the coerced value is collected.
        Call this only after all `patch_parser` calls have been made.

        Returns
        -------
        mapping
            Dict keyed by env var name.
            Each value is a list of `(section, dest, coerced_value)` tuples —
            one per patched parser that registered the dest.
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
                    except _CONVERSION_ERRORS:
                        # A value that cannot be coerced is reported by `problems`,
                        # and left out here because there is nothing to map it to.
                        continue
                    result.setdefault(env_key, []).append((section, dest, coerced))
        return result

    def patch_parser(
        self,
        parser: argparse.ArgumentParser,
        *,
        use_section: bool = True,
        merge_handlers: dict[str, Callable[[Any, Any], Any]] | None = None,
    ) -> None:
        """Inject config defaults and env-var overrides into an argparse parser.

        For each argument in `parser`, values are accumulated from `_configs` in order
        (later files win) and then from `_env`.
        When a merge handler is registered for a dest
        and both an accumulated value and an incoming value are non-`None`,
        the handler is called instead of the plain "incoming replaces accumulated" rule.

        A value that cannot be used is recorded for `problems` and skipped,
        leaving the argument's own default in place, instead of raising.

        Parameters
        ----------
        parser
            Argparse parser to patch.
            Argument defaults are mutated in place.
        use_section
            Set to `False` for the top-level parser.
            If `True`, the last word of the parser's `prog` is used as section name
            for config files (e.g., the `[build]` section) and env vars (e.g., `STEPUP_BUILD_`).
            Argparse prefixes a subparser's `prog` with its parent's,
            so `"stepup build"` and `"build"` both select the `"build"` section.
            A subcommand that is an alias of another must therefore set its `prog`
            to that of the subcommand it aliases, to share its configuration.
            If `False`, top-level config keys (no section) and
            env vars with no section infix (e.g., `STEPUP_`) are used.
        merge_handlers
            Per-dest callables `fn(accumulated, incoming) -> merged` called
            when both an accumulated value and a new value are available.
            Without a handler the incoming value replaces the accumulated one.
        """
        handlers = merge_handlers or {}
        section = self._section(parser) if use_section else None

        # Navigate each config to the requested section up front.
        config_views: list[tuple[Path, dict]] = []
        for path, config in self._configs:
            data = config
            if section is not None:
                data = data.get(section, {})
                if not isinstance(data, dict):
                    # The value sits at the top level of the file, where the section belongs.
                    self._errors.append(
                        ConfigProblem(
                            _not_a_section(self._section_path(path, section)),
                            path=path,
                            key=section,
                        )
                    )
                    data = {}
            # Sub-tables of a section have no meaning, while those at the top level are
            # the sections of other parsers, whose keys are not this parser's concern.
            flat = {}
            for key, value in data.items():
                if not isinstance(value, dict):
                    flat[key] = value
                elif section is not None:
                    self._unknown_keys.append((path, section, key))
            config_views.append((path, flat))

        action_map, positional_dests = self._actions(parser)
        self._positionals.setdefault(section, set()).update(positional_dests)
        for dest, action in action_map.items():
            value = None

            # Apply file configs in priority order.
            for path, data in config_views:
                incoming = data.pop(dest, None)
                if incoming is not None:
                    try:
                        incoming = self._coerce_type(incoming, action)
                    except _CONVERSION_ERRORS as exc:
                        location = self._location_phrase(path, section)
                        self._errors.append(
                            ConfigProblem(
                                f"{dest} {location}: {_conversion_detail(exc)}",
                                path=path,
                                section=section,
                                key=dest,
                            )
                        )
                        continue
                    handler = None if value is None else handlers.get(dest)
                    value = incoming if handler is None else handler(value, incoming)

            # Overlay environment variable (highest priority).
            env_key = self._env_key(section, dest)
            env_value = self._env.get(env_key)
            if env_value is not None:
                try:
                    incoming = self._coerce_type(env_value, action)
                except _CONVERSION_ERRORS as exc:
                    self._errors.append(ConfigProblem(_conversion_detail(exc), env_var=env_key))
                else:
                    handler = None if value is None else handlers.get(dest)
                    value = incoming if handler is None else handler(value, incoming)

            if value is not None:
                if action.nargs == "?":
                    # nargs="?" options (e.g. --perf) have two fallback slots:
                    # `default` (flag absent) and `const` (flag given bare).
                    # A config/env value should win in both cases,
                    # not just when the flag is omitted entirely.
                    action.const = value
                    action.default = value
                else:
                    action.default = value

        # Whatever is left over is not an option of this parser.
        # Which section does support it, if any, is only known once every parser is patched,
        # so the verdict is left to `problems`.
        for path, data in config_views:
            self._unknown_keys.extend((path, section, key) for key in data)

        self._patches.append((section, action_map))

    def check(self) -> list[str]:
        """Return a message for every problem found in the configuration.

        Returns
        -------
        messages
            The `message` of every problem returned by `problems`.
        """
        return [problem.message for problem in self.problems()]

    def problems(self) -> list[ConfigProblem]:
        """Return every problem found in the configuration.

        Call this only after all `patch_parser` calls have been made:
        whether a key or a section is supported depends on the parsers that were patched.

        Returns
        -------
        problems
            One problem per thing to fix, in the order the problems were found
            and without duplicate messages.
            Empty when the configuration is sound.
        """
        dests: dict[str | None, set[str]] = {}
        for section, actions in self._patches:
            dests.setdefault(section, set()).update(actions)
        sections = sorted(section for section in dests if section is not None)

        problems = list(self._errors)
        for path, section, key in self._unknown_keys:
            if section is None and key in dests:
                # A section name that holds a scalar instead of a table.
                # The parser of that section already reported it as such.
                continue
            hint = self._key_hint(path, key, section, dests)
            location = self._location_phrase(path, section)
            problems.append(
                ConfigProblem(
                    f"unsupported key {key!r} {location}{hint}",
                    path=path,
                    section=section,
                    key=key,
                )
            )

        # Sections that no parser claims. Only known once every parser is patched,
        # which is also why they cannot be detected in `patch_parser` itself.
        for path, config in self._configs:
            for key, value in config.items():
                if not isinstance(value, dict) or key in dests:
                    continue
                section_path = self._section_path(path, key)
                owners = {owner for owner, owner_dests in dests.items() if key in owner_dests}
                if len(owners) > 0:
                    # A setting written as a table, the counterpart of `_not_a_section`.
                    detail = f"{_not_a_setting(section_path)} {self._join_locations(path, owners)}"
                else:
                    hint = _hint(difflib.get_close_matches(key, sections))
                    detail = f"unknown section [{section_path}]{hint}"
                problems.append(ConfigProblem(detail, path=path, section=key))

        unique = {}
        for problem in problems:
            unique.setdefault(problem.message, problem)
        return list(unique.values())

    def _key_hint(
        self, path: Path, key: str, section: str | None, dests: dict[str | None, set[str]]
    ) -> str:
        """Suggest where an unsupported key belongs, or how it is spelled correctly.

        Parameters
        ----------
        path
            The config file the key was found in, which decides how a section is named.
        key
            The unsupported key.
        section
            The section the key was found in, `None` for the top level.
        dests
            The supported keys of each section, as collected from the patched parsers.

        Returns
        -------
        hint
            A suffix for the error message, empty when nothing plausible was found.
        """
        if key in self._positionals.get(section, ()):
            return " (a positional command-line argument, which cannot be configured)"
        owners = {owner for owner, owner_dests in dests.items() if key in owner_dests} - {section}
        if len(owners) > 0:
            return f" (it belongs {self._join_locations(path, owners)})"
        # A key equal to a supported one, without ending up in `owners`,
        # is a table where a setting is expected.
        # Suggesting its own name back is of no use.
        candidates = sorted(
            {dest for owner_dests in dests.values() for dest in owner_dests} - {key}
        )
        suggestions = []
        for match in difflib.get_close_matches(key, candidates):
            owners = {owner for owner, owner_dests in dests.items() if match in owner_dests}
            if section in owners:
                suggestions.append(repr(match))
            else:
                suggestions.append(f"{match!r} {self._join_locations(path, owners)}")
        if len(suggestions) == 0:
            return ""
        return f" (did you mean {' or '.join(suggestions)}?)"

    def _join_locations(self, path: Path, sections: set[str | None]) -> str:
        """Combine the places where a key is supported into one phrase."""
        return " or ".join(sorted(self._location_phrase(path, section) for section in sections))


def config_subcommand(subparsers, loader: ConfigLoader) -> ToolFunc:
    """Define command-line arguments for the config tool.

    Parameters
    ----------
    subparsers
        The subparser to add the config tool to.
    loader
        The configuration loader used to read and merge configuration sources.

    Returns
    -------
    tool_func
        The function to call with the parsed args to execute the config command.
    """
    subparsers.add_parser(
        "config",
        help="Print the effective StepUp configuration as TOML.",
    )

    def config_tool(args: argparse.Namespace) -> None:
        problems = loader.problems()
        remaining = _render_config(loader, problems)
        if len(remaining) > 0:
            # Problems go to stderr, so that the TOML on stdout stays usable in a pipeline.
            print_config_problems(remaining)
        if len(problems) > 0:
            # This is the one tool that renders its own errors, because rendering them
            # on the line of the setting they concern is what the tool is for.
            sys.exit(ReturnCode.INTERNAL.value)

    return config_tool


def format_config_problems(problems: list[ConfigProblem]) -> str:
    """Combine the problems reported by `ConfigLoader.problems` into one error message.

    Parameters
    ----------
    problems
        The problems to report.

    Returns
    -------
    text
        A multi-line message with one indented line per problem.
    """
    lines = ["Problems with the StepUp configuration:"]
    lines.extend(f"  {problem.message}" for problem in problems)
    return "\n".join(lines)


def print_config_problems(problems: list[ConfigProblem], hint: str = "") -> None:
    """Print the problems found in the configuration on standard error.

    Parameters
    ----------
    problems
        The problems to report, as returned by `ConfigLoader.problems`.
    hint
        A closing line suggesting what to do about the problems, omitted when empty.
    """
    text = Text("Problems with the StepUp configuration:")
    for problem in problems:
        text.append(f"\n  {problem.location}: ", style="bold")
        text.append(problem.detail, style=ERROR_STYLE)
    if hint != "":
        text.append(f"\n{hint}")
    print_error(text)


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


def _render_config(loader: ConfigLoader, problems: list[ConfigProblem]) -> list[ConfigProblem]:
    """Print the merged configuration as TOML on standard output, problems included.

    Parameters
    ----------
    loader
        The configuration loader, with every parser patched into it.
    problems
        The problems to show, each on the line of the setting, section or config file
        it was found in.

    Returns
    -------
    remaining
        The problems that have no line of their own to be shown on,
        e.g. one about a setting that a config file with a higher priority overrides.
    """
    lines: list[str] = []
    anchors: dict[tuple, _Anchor] = {}

    env_prefix = loader.prefix.upper() + "_"
    short_paths = {}

    lines.append("# Config files (lowest to highest priority):")
    for path in loader.config_paths:
        short = _short_path(path)
        tag = "FOUND:  " if Path(path).expanduser().is_file() else "MISSING:"
        anchors["file", str(path)] = _Anchor(len(lines))
        lines.append(f"#   {tag} {short}")
        short_paths[str(path)] = short
    lines.append(f"# Environment variables: {env_prefix}*")

    provenance = loader.dump_with_provenance()
    env_map = loader.env_to_toml_map()
    all_env_vars = loader.relevant_env_vars()

    # Overlay env var values onto the provenance dict (env vars win, sourced as "$VAR").
    merged: dict[str, dict[str, tuple[Any, str]]] = {k: dict(v) for k, v in provenance.items()}
    for env_key, matches in env_map.items():
        for section, dest, coerced in matches:
            section_key = section or ""
            if section_key not in merged:
                merged[section_key] = {}
            merged[section_key][dest] = (coerced, f"${env_key}")

    top = merged.get("", {})
    sections = {k: v for k, v in merged.items() if k and v}

    if not top and not sections and not all_env_vars:
        lines.append("")
        lines.append("# No configuration found.")
    else:
        if top:
            lines.append("")
            for key in sorted(top):
                value, source = top[key]
                anchors["setting", "", key, source] = _Anchor(len(lines))
                lines.append(f"{key} = {_toml_value(value)}  # {short_paths.get(source, source)}")

        for section in sorted(sections):
            lines.append("")
            # A section header has no comment yet, so the problem must open one,
            # to keep the output valid TOML.
            anchors["section", section] = _Anchor(len(lines), comment=True)
            lines.append(f"[{section}]")
            for key in sorted(sections[section]):
                value, source = sections[section][key]
                anchors["setting", section, key, source] = _Anchor(len(lines))
                lines.append(f"{key} = {_toml_value(value)}  # {short_paths.get(source, source)}")

        _render_env_vars(lines, anchors, env_prefix, all_env_vars, loader.known_env_vars())

    spans, remaining = _inline_problems(lines, anchors, problems)
    _print_toml(lines, spans)
    return remaining


_ERROR_MARK = "<-- ERROR: "
"""What sets a problem apart from the comment naming the source of a setting."""


@attrs.define(frozen=True)
class _Anchor:
    """A line in the rendered configuration that a problem can be shown on."""

    index: int = attrs.field()
    """The position of the line in the rendered output."""

    comment: bool = attrs.field(default=False)
    """Whether a problem must open a comment on this line, to keep the output valid TOML."""


CORE_ENV_VARS = frozenset(
    {
        # These are intended for end users.
        "STEPUP_MAX_OUTPUT_SIZE",
        "STEPUP_PATH_FILTER",
        "STEPUP_ROOT",
        "STEPUP_SYNC_RPC_TIMEOUT",
        # The following are only for internal use and are treated as unrecognized,
        # because setting them has no effect on the configuration.
        # "STEPUP_DIRECTOR_SOCKET",
        # "STEPUP_JOB_I",
        # "STEPUP_REPORTER_SOCKET",
        # "STEPUP_STEP_INP_DIGEST",
        # "STEPUP_STEP_NEED",
    }
)
"""The variables StepUp Core acts on without a subcommand defining a setting for them.

Maintained by hand: nothing derives it from the places that read these variables.
A variable with the prefix that is in neither this set nor `ConfigLoader.known_env_vars`
does nothing, which is how a typo in a variable name becomes visible.

Extension packages are not covered:
their settings are recognized through their patched parsers,
but the variables they use internally are not listed here.
"""


def _render_env_vars(
    lines: list[str],
    anchors: dict[tuple, _Anchor],
    env_prefix: str,
    env_vars: dict[str, str],
    known: set[str],
) -> None:
    """Append the environment variables of the prefix as comments, grouped by what they do.

    Parameters
    ----------
    lines
        The rendered TOML, extended in place.
    anchors
        Every place a problem can be shown at, extended in place.
    env_prefix
        The prefix that the listed environment variables share, underscore included.
    env_vars
        The environment variables to list, keyed by name.
    known
        The names that the patched parsers recognize, as returned by
        `ConfigLoader.known_env_vars`.
    """
    groups = [
        ("# Configuration environment variables:", "config"),
        ("# StepUp Core module environment variables:", "core"),
        ("# Unrecognized environment variables, without effect:", "unknown"),
    ]
    selection = {}
    for env_key in env_vars:
        if env_key in known:
            group = "config"
        elif env_key in CORE_ENV_VARS:
            group = "core"
        else:
            group = "unknown"
        selection.setdefault(group, []).append(env_key)
    for header, group in groups:
        selected = selection.get(group)
        if selected is None:
            continue
        lines.append("")
        lines.append(header)
        for env_key in sorted(selected):
            anchors["env", env_key] = _Anchor(len(lines))
            lines.append(f"#   {env_key} = {_toml_value(env_vars[env_key])}")


def _anchor(anchors: dict[tuple, _Anchor], problem: ConfigProblem) -> tuple[_Anchor | None, str]:
    """Find the rendered line a problem belongs to.

    Parameters
    ----------
    anchors
        Every place a problem can be shown at, keyed by a tuple
        whose first item says what kind of place it is.
    problem
        The problem to place.

    Returns
    -------
    anchor
        The line to show the problem on, `None` when there is none.
    text
        The problem as it is to be shown on that line,
        which repeats the location only when the line itself does not name it.
    """
    if problem.env_var is not None:
        return anchors.get(("env", problem.env_var)), problem.detail
    source = str(problem.path)
    if problem.key is not None:
        # Not found when another config file overrides the setting,
        # in which case the rendered line names that other file and not this problem's.
        return anchors.get(("setting", problem.section or "", problem.key, source)), problem.detail
    if problem.section is not None:
        return anchors.get(("section", problem.section)), problem.message
    return anchors.get(("file", source)), problem.detail


def _inline_problems(
    lines: list[str], anchors: dict[tuple, _Anchor], problems: list[ConfigProblem]
) -> tuple[list[tuple[int, int, int]], list[ConfigProblem]]:
    """Append every problem that has a line of its own to that line.

    Parameters
    ----------
    lines
        The rendered TOML, extended in place.
    anchors
        Every place a problem can be shown at, as used by `_anchor`.
    problems
        The problems to show.

    Returns
    -------
    spans
        The `(line index, start column, stop column)` of each appended problem.
    remaining
        The problems that no line of their own was found for.
    """
    spans = []
    remaining = []
    taken: set[int] = set()
    for problem in problems:
        anchor, text = _anchor(anchors, problem)
        # A line holds one problem at most.
        # A second one would have to reopen the comment that the first one already sits in,
        # so it is reported below the configuration instead.
        if anchor is None or anchor.index in taken:
            remaining.append(problem)
            continue
        taken.add(anchor.index)
        line = lines[anchor.index]
        mark = f"  {'# ' if anchor.comment else ''}{_ERROR_MARK}{text}"
        lines[anchor.index] = line + mark
        # The two spaces that set the problem apart stay unstyled.
        spans.append((anchor.index, len(line) + 2, len(line) + len(mark)))
    return spans, remaining


def _print_toml(lines: list[str], spans: list[tuple[int, int, int]]) -> None:
    """Print TOML lines with syntax highlighting, with the given spans marked as problems.

    Parameters
    ----------
    lines
        The lines to print, without line separators.
    spans
        The `(line index, start column, stop column)` of every span to mark.
    """
    toml_text = "\n".join(lines) + "\n"
    # Highlighting into a `Text` instead of printing a `Syntax` allows the problems
    # to be styled on top of the syntax highlighting.
    text = Syntax(toml_text, "toml", theme="ansi_dark", word_wrap=False).highlight(toml_text)
    line_starts = []
    offset = 0
    for line in lines:
        line_starts.append(offset)
        offset += len(line) + 1
    for index, start, stop in spans:
        text.stylize(ERROR_STYLE, line_starts[index] + start, line_starts[index] + stop)
    Console(soft_wrap=True).print(text)
