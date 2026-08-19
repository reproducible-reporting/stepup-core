# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Multi-source configuration loader for StepUp and its extensions.

Config files are loaded at construction into an ordered list;
the environment is also preloaded.
No merging takes place until `patch_parser` is called.
That method applies defaults to any argparse parser one option at a time,
working through the config list in order (later files win)
and overlaying the environment last.

`patch_parser` is the only place where the sources are merged.
It records what it decided for every setting,
which is what `effective_config` reports,
so that what a tool receives and what `stepup config` shows can never disagree.

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

# Main parser, top-level config keys, env vars: STEPUP_<DEST>
loader.patch_parser(main_parser, top_level=True)

# Tool subparser, section taken from the last word of its prog ("mytool"),
# env vars: STEPUP_MYTOOL_<DEST>
loader.patch_parser(mytool_parser, merge_handlers={"paths": merge_paths})

# Once every parser is patched: report everything that is wrong with the configuration.
problems = loader.problems()
if len(problems) > 0:
    print_config_problems(problems)
```

## Error Handling

Loading and patching never raise on a bad configuration:
problems are recorded and returned together by `problems`,
so that a user gets the complete list in one go instead of one problem per run.
Recognizing an unsupported key or an unknown section also requires
every parser to have been patched first.

An environment variable is held to a weaker standard than a config file:
a name that no parser recognizes is never a problem,
because the prefix is shared with the variables that configure internals.
`recognized_env_vars` reports which names mean something, and nothing more.

The `stepup config` subcommand, which renders the merged result of all of this as TOML,
lives in `config_tool.py` and is a plain client of the public API below.

## Argparse Internals

Reading the arguments of a parser and recognizing the actions that need a conversion of their
own relies on undocumented argparse names (`ArgumentParser._actions`, `_SubParsersAction`,
`_HelpAction`, `_VersionAction`, `_StoreTrueAction`, `_StoreFalseAction` and `_CountAction`),
which is where a new Python version is most likely to break this module.
"""

import argparse
import difflib
import os
import tomllib
from collections.abc import Callable
from typing import Any

import attrs
from path import Path
from rich.text import Text

from .exceptions import ConfigError, ConsistencyError
from .path import short_path
from .tool import ERROR_STYLE, print_error
from .utils import to_bool

__all__ = (
    "ConfigFile",
    "ConfigLoader",
    "ConfigLocation",
    "ConfigProblem",
    "ConfigValue",
    "format_config_problems",
    "print_config_problems",
)


_CONVERSION_ERRORS = (ValueError, TypeError, ArithmeticError, argparse.ArgumentTypeError)
"""Exceptions with which a conversion may reject a config value.

`ArithmeticError` covers `decimal.InvalidOperation`, raised by a `Decimal` option.
Any other exception points at a bug in the conversion function instead of at the value,
and keeps its traceback.
"""

_PROBLEMS_HEADER = "Problems with the StepUp configuration:"
"""The first line of a report of everything that is wrong with the configuration."""


#
# Error message helpers
#


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


def _expected_section(section_path: str) -> str:
    """Report a section that holds a plain value instead of a table with settings."""
    return f"'{section_path}' is configured as a value, but a section [{section_path}] is expected"


def _expected_value(section_path: str) -> str:
    """Report a setting that holds a table with settings instead of a plain value.

    The returned message is a fragment,
    to be completed with the location phrase of the section the setting belongs to.
    """
    return f"'{section_path}' is configured as a section, but a value is expected"


def _hint(suggestions: list[str]) -> str:
    """Turn the suggestions for a mistyped name into a suffix for an error message.

    Parameters
    ----------
    suggestions
        The suggestions as they are to be shown, already formatted,
        because a suggestion may carry a location phrase in addition to the name.

    Returns
    -------
    hint
        The suffix to append to the error message,
        empty when there are no suggestions, so it can be appended unconditionally.
    """
    if len(suggestions) == 0:
        return ""
    return " (did you mean " + " or ".join(suggestions) + "?)"


#
# Value objects
#


@attrs.define(frozen=True)
class ConfigLocation:
    """A place in the configuration, at whatever granularity is known.

    A location is one of four things:
    an environment variable, a config file, a section of a config file,
    or a setting of a config file.
    The four constructors say which, so that a call site does not have to spell out
    the fields that do not apply.
    Locations are hashable, so `config` can key the lines it renders by the location
    they show, and look up the location of a problem to display it on its own line.

    Raises
    ------
    ConsistencyError
        When neither `path` nor `env_var` is given, which leaves the location nameless.
    """

    path: Path | None = attrs.field(default=None, converter=attrs.converters.optional(Path))
    """The config file, `None` for an environment variable."""

    section: str | None = attrs.field(default=None)
    """The section of the config file, `None` for the top level of the file."""

    key: str | None = attrs.field(default=None)
    """The setting in the section, `None` when the whole section or file is meant."""

    env_var: str | None = attrs.field(default=None)
    """The environment variable, `None` for a config file."""

    def __attrs_post_init__(self) -> None:
        if self.path is None and self.env_var is None:
            raise ConsistencyError("Config location without path or env var.")

    @classmethod
    def of_env(cls, env_var: str) -> "ConfigLocation":
        """Locate an environment variable."""
        return cls(env_var=env_var)

    @classmethod
    def of_file(cls, path: Path) -> "ConfigLocation":
        """Locate a config file as a whole."""
        return cls(path=path)

    @classmethod
    def of_section(cls, path: Path, section: str) -> "ConfigLocation":
        """Locate a section of a config file."""
        return cls(path=path, section=section)

    @classmethod
    def of_setting(cls, path: Path, section: str | None, key: str) -> "ConfigLocation":
        """Locate a setting of a config file, with `section` `None` for the top level."""
        return cls(path=path, section=section, key=key)

    def __str__(self) -> str:
        """The config file or environment variable to look at."""
        return f"${self.env_var}" if self.env_var is not None else str(short_path(self.path))


@attrs.define(frozen=True)
class ConfigProblem:
    """Something wrong with the configuration, and where it was found."""

    detail: str = attrs.field()
    """What is wrong, without naming the config file or environment variable."""

    location: ConfigLocation = attrs.field()
    """The place the problem was found, as precisely as it is known."""

    @property
    def message(self) -> str:
        """The problem on a single line, location included."""
        return f"{self.location}: {self.detail}"


@attrs.define(frozen=True)
class ConfigValue:
    """A setting of the effective configuration, and where it came from."""

    value: Any = attrs.field()
    """The value in the type the parser will use, coerced from what the source held."""

    location: ConfigLocation = attrs.field()
    """The source of highest priority that contributed to this value.

    With a merge handler, sources of lower priority contributed as well,
    and this is the last one that did.
    """


@attrs.define(frozen=True)
class ConfigFile:
    """One of the config files of a loader, as it was found at construction."""

    path: Path = attrs.field(converter=Path)
    """The config file, as it was given, with `~` still unexpanded.

    That form already says where the file is and is shorter than the expanded one,
    which is what makes it the better name to show in a message.
    """

    data: dict = attrs.field()
    """The settings found in the file, keyed by name, exactly as they were read.

    Empty when the file is absent, and also when it is present but unreadable,
    in which case the loader has recorded a problem about it.
    """

    exists: bool = attrs.field()
    """Whether the file was there when the loader read it."""


@attrs.define(frozen=True)
class _SectionView:
    """What one config file holds in one section, with the nested tables left out."""

    path: Path = attrs.field(converter=Path)
    """The config file the settings come from, named as in `ConfigFile.path`."""

    data: dict = attrs.field()
    """The settings of the section, keyed by name, exactly as they were read."""


@attrs.define
class _Patch:
    """What the patched parsers contribute to one config section, and what it amounts to.

    There is a single record per section, even when more than one parser patches it,
    an alias of a subcommand being the usual reason for that.
    """

    actions: dict[str, argparse.Action] = attrs.field(factory=dict)
    """The action of every configurable argument, keyed by dest."""

    positional_dests: set[str] = attrs.field(factory=set)
    """The dests of the positional arguments, which cannot be configured."""

    values: dict[str, ConfigValue] = attrs.field(factory=dict)
    """The value the sources settled on, for each dest that any of them set."""


#
# Argparse helpers
#
# These do not read the configuration and are therefore not methods of `ConfigLoader`.
#


def _section_of_parser(parser: argparse.ArgumentParser) -> str:
    """Derive the config section name from a parser's `prog`.

    Argparse prefixes the `prog` of a subparser with that of its parent,
    e.g. `"stepup clean"` for the `clean` subcommand.
    Only the last word is the section name,
    so that `stepup clean` reads `[clean]` and not `[stepup clean]`.
    """
    return parser.prog.rsplit(" ", 1)[-1]


def _patch_of(parser: argparse.ArgumentParser) -> _Patch:
    """Split the user-facing arguments of `parser` into configurable and CLI-only ones.

    The `--help` and `--version` actions and the subparser action are left out:
    they act while the command line is parsed and have no value to configure.

    Returns
    -------
    patch
        A patch whose `actions` hold the arguments that a config file or an environment
        variable can set, and whose `positional_dests` hold the arguments that have no
        `option_strings` and can only be given on the command line.
        Its `values` are empty, because nothing has been merged yet.
    """
    patch = _Patch()
    for action in parser._actions:
        if isinstance(
            action,
            (argparse._HelpAction, argparse._VersionAction, argparse._SubParsersAction),
        ):
            continue
        if action.option_strings:
            patch.actions[action.dest] = action
        else:
            patch.positional_dests.add(action.dest)
    return patch


def _coerce_value(raw: Any, action: argparse.Action) -> Any:
    """Parse a raw config value into the target Python type and validate choices.

    Boolean flags (`store_true`, `store_false`, `BooleanOptionalAction`) are converted
    with `to_bool` and count actions with `int`.
    Any other option is converted with the action's `type` callable when it has one,
    and taken as it is otherwise.
    After coercion, the value is validated against `action.choices` when present.

    An argument that takes several values (`nargs` set to `"*"`, `"+"` or a number)
    can therefore only be configured when it has no `type`,
    in which case the TOML list is taken as it is.
    Argparse calls `type` on each element of such an argument,
    while a configured value is converted as a whole,
    so the conversion receives the entire list and the failure that usually follows
    is reported as a bad value rather than as an unsupported argument.

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
        value = to_bool(raw)
    elif isinstance(action, argparse._CountAction):
        value = int(raw)
    elif action.type is not None:
        value = action.type(raw)
    else:
        value = raw
    if action.choices is not None and value not in action.choices:
        # The dest is not named here, because the caller opens the message
        # with the setting or the environment variable that holds the value.
        raise ValueError(f"invalid value {value!r}: choose from {list(action.choices)}")
    return value


def _merge(value: Any, incoming: Any, handler: Callable[[Any, Any], Any] | None) -> Any:
    """Combine the value accumulated so far with the one of the next source.

    Parameters
    ----------
    value
        What the sources of lower priority amount to,
        `None` when none of them set the option.
    incoming
        The already coerced value of the next source.
    handler
        The merge handler registered for the dest, `None` when there is none.

    Returns
    -------
    merged
        The merged value, which is `incoming` when there is nothing to merge it with.
        A handler is skipped while `value` is `None`,
        so it never sees the argparse default as its left operand:
        it only ever combines values that were actually configured.
    """
    if value is None or handler is None:
        return incoming
    return handler(value, incoming)


def _set_default(action: argparse.Action, value: Any) -> None:
    """Make `value` what the parser falls back on when the option is not given."""
    if action.nargs == "?":
        # nargs="?" options (e.g. --perf) have two fallback slots:
        # `default` (flag absent) and `const` (flag given bare).
        # A config/env value should win in both cases,
        # not just when the flag is omitted entirely.
        action.const = value
    action.default = value


@attrs.define
class ConfigLoader:
    """Load configuration from files and environment, then patch argparse parsers.

    At construction each config file is loaded into a `ConfigFile` of its own in `_configs`;
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
        Ordered list of config file paths, from lowest to highest priority.
        The special filename `pyproject.toml` is loaded from the section derived from `prefix`.
    environ
        Environment dict to read from.
        `None` (default) snapshots `os.environ` at construction time.
        Pass an explicit dict (including `{}`) in tests to avoid depending on the real environment.
    """

    _prefix: str = attrs.field()
    _config_paths: list[str] = attrs.field(factory=list, kw_only=True)
    _environ: dict[str, str] | None = attrs.field(default=None)
    _configs: list[ConfigFile] = attrs.field(init=False, factory=list)
    _env: dict[str, str] = attrs.field(init=False, factory=dict)
    _patches: dict[str | None, _Patch] = attrs.field(init=False, factory=dict)
    _errors: list[ConfigProblem] = attrs.field(init=False, factory=list)
    _unknown_keys: list[ConfigLocation] = attrs.field(init=False, factory=list)
    _table_keys: list[ConfigLocation] = attrs.field(init=False, factory=list)

    def __attrs_post_init__(self) -> None:
        for path in self._config_paths:
            try:
                data = self._load_file(path)
            except ConfigError as exc:
                # The file is there, but nothing can be read from it.
                self._errors.append(ConfigProblem(str(exc), ConfigLocation.of_file(path)))
                data = {}
            exists = data is not None
            self._configs.append(ConfigFile(path, data if exists else {}, exists))
        self._env = dict(os.environ) if self._environ is None else self._environ

    #
    # Naming and addressing
    #

    @property
    def env_prefix(self) -> str:
        """The prefix that every environment variable of this loader starts with.

        The uppercase prefix with a trailing underscore, e.g. `STEPUP_`.
        """
        return self._prefix.upper() + "_"

    @property
    def toml_table(self) -> str:
        """The table of `pyproject.toml` that holds the configuration, e.g. `tool.stepup`."""
        return f"tool.{self._prefix.lower()}"

    @property
    def config_files(self) -> list[ConfigFile]:
        """The config files, in priority order (lowest to highest)."""
        return list(self._configs)

    def _env_var_name(self, section: str | None, dest: str) -> str:
        """Compute the environment variable name for a (section, dest) pair.

        Dots and hyphens in the section name are converted to underscores.
        """
        section_str = (section.upper().replace(".", "_").replace("-", "_") + "_") if section else ""
        return f"{self.env_prefix}{section_str}{dest.upper()}"

    def _section_path(self, path: Path, section: str | None) -> str:
        """Return the dotted TOML path of `section` in the config file at `path`.

        The path is empty for the top level of a regular config file,
        because the settings there are not nested in any table.
        """
        parts = []
        if path.name == "pyproject.toml":
            parts.append(self.toml_table)
        if section is not None:
            parts.append(section)
        return ".".join(parts)

    def _section_phrase(self, path: Path, section: str | None) -> str:
        """Name the place in a config file that a key belongs to, preposition included."""
        section_path = self._section_path(path, section)
        return f"in section [{section_path}]" if section_path else "at the top level"

    def _join_section_phrases(self, path: Path, sections: set[str | None]) -> str:
        """Combine the places where a key is supported into one phrase."""
        return " or ".join(sorted(self._section_phrase(path, section) for section in sections))

    #
    # Loading
    #

    def _load_file(self, config_path: str) -> dict | None:
        """Load a TOML config file, without filtering.

        Parameters
        ----------
        config_path
            Path to the config file, with `~` still to be expanded.

        Returns
        -------
        data
            Full dict of the config file, with no parser-key filtering.
            For `pyproject.toml`, the dict of the section derived from the prefix,
            e.g. `tool.stepup`.
            `None` when the file does not exist,
            which is how the caller tells an absent file from an empty one.

        Raises
        ------
        ConfigError
            When the file cannot be read, parsed, or navigated to the expected section.
            The message says what is wrong without naming the file.
        """
        path = Path(config_path).expanduser()
        if not path.is_file():
            return None
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
            parts = self.toml_table.split(".")
            for i, part in enumerate(parts):
                data = data.get(part, {})
                if not isinstance(data, dict):
                    raise ConfigError(_expected_section(".".join(parts[: i + 1])))
        return data

    #
    # Patching
    #

    def _section_views(self, section: str | None) -> list[_SectionView]:
        """Navigate every config file to `section` and flatten what it holds there.

        A section that holds a plain value is recorded as a problem on the spot,
        while the keys of the nested tables are set aside for `problems`,
        because whether another parser supports them is not known yet.

        Parameters
        ----------
        section
            The section to read, `None` for the top level of the files.

        Returns
        -------
        views
            One view per config file, in the priority order of `_configs`,
            each holding the plain values of the section, keyed by setting name.
            A file without such a section yields an empty view.
        """
        views = []
        for config_file in self._configs:
            data = config_file.data
            if section is not None:
                data = data.get(section, {})
                if not isinstance(data, dict):
                    self._errors.append(
                        ConfigProblem(
                            _expected_section(self._section_path(config_file.path, section)),
                            # The offending value sits at the top level of the file,
                            # where it is a setting whose name is that of the section.
                            ConfigLocation.of_setting(config_file.path, None, section),
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
                    self._table_keys.append(
                        ConfigLocation.of_setting(config_file.path, section, key)
                    )
            views.append(_SectionView(config_file.path, flat))
        return views

    def patch_parser(
        self,
        parser: argparse.ArgumentParser,
        *,
        top_level: bool = False,
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

        Whatever the sources settle on is recorded for `effective_config`,
        so that no other method has to work out what a setting amounts to.

        Parameters
        ----------
        parser
            Argparse parser to patch.
            Argument defaults are mutated in place.
        top_level
            Set to `True` for the top-level parser,
            whose settings are top-level config keys (no section)
            and env vars with no section infix (e.g. `STEPUP_`).
            If `False` (default), the last word of the parser's `prog` is used as section name
            for config files (e.g., the `[build]` section) and env vars (e.g., `STEPUP_BUILD_`).
            Argparse prefixes a subparser's `prog` with its parent's,
            so `"stepup build"` and `"build"` both select the `"build"` section.
            A subcommand that is an alias of another must therefore set its `prog`
            to that of the subcommand it aliases, to share its configuration.
        merge_handlers
            Per-dest callables `fn(accumulated, incoming) -> merged` called
            when both an accumulated value and a new value are available.
            Without a handler the incoming value replaces the accumulated one.
        """
        handlers = merge_handlers or {}
        section = None if top_level else _section_of_parser(parser)
        views = self._section_views(section)

        parser_patch = _patch_of(parser)
        values: dict[str, ConfigValue] = {}
        for dest, action in parser_patch.actions.items():
            value = None
            location = None

            # Apply file configs in priority order.
            for view in views:
                # The `pop` also marks the key as consumed:
                # what is left in the view afterwards is what this parser has no argument for.
                incoming = view.data.pop(dest, None)
                if incoming is None:
                    continue
                try:
                    incoming = _coerce_value(incoming, action)
                except _CONVERSION_ERRORS as exc:
                    phrase = self._section_phrase(view.path, section)
                    self._errors.append(
                        ConfigProblem(
                            f"{dest} {phrase}: {_conversion_detail(exc)}",
                            ConfigLocation.of_setting(view.path, section, dest),
                        )
                    )
                    continue
                value = _merge(value, incoming, handlers.get(dest))
                location = ConfigLocation.of_setting(view.path, section, dest)

            # Overlay environment variable (highest priority).
            env_var = self._env_var_name(section, dest)
            env_value = self._env.get(env_var)
            if env_value is not None:
                try:
                    incoming = _coerce_value(env_value, action)
                except _CONVERSION_ERRORS as exc:
                    self._errors.append(
                        ConfigProblem(_conversion_detail(exc), ConfigLocation.of_env(env_var))
                    )
                else:
                    value = _merge(value, incoming, handlers.get(dest))
                    location = ConfigLocation.of_env(env_var)

            if location is not None:
                _set_default(action, value)
                values[dest] = ConfigValue(value, location)

        # Whatever is left over is not an option of this parser.
        # Which section does support it, if any, is only known once every parser is patched,
        # so the verdict is left to `problems`.
        for view in views:
            self._unknown_keys.extend(
                ConfigLocation.of_setting(view.path, section, key) for key in view.data
            )

        # Two parsers may share a section, an alias of a subcommand being the usual reason.
        # Their action objects are distinct but structurally identical,
        # and the first one recorded is the one kept.
        patch = self._patches.setdefault(section, _Patch())
        for dest, action in parser_patch.actions.items():
            patch.actions.setdefault(dest, action)
        patch.positional_dests.update(parser_patch.positional_dests)
        patch.values.update(values)

    #
    # Inspecting the result
    #

    def prefixed_env_vars(self) -> dict[str, str]:
        """Return the environment variables whose names start with `env_prefix`."""
        return {k: v for k, v in self._env.items() if k.startswith(self.env_prefix)}

    def recognized_env_vars(self) -> set[str]:
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
            self._env_var_name(section, dest)
            for section, patch in self._patches.items()
            for dest in patch.actions
        }

    def effective_config(self) -> dict[str | None, dict[str, ConfigValue]]:
        """Return the configuration as the patched parsers received it.

        Every setting that a parser claims is reported exactly as `patch_parser` decided it,
        merge handlers and coercion included.
        A key that no parser claims, or whose value was rejected,
        is reported as the config file holds it,
        so that `config` can show it on a line together with the problem it causes.

        Call this only after all `patch_parser` calls have been made,
        because an unpatched parser has decided nothing yet.

        Returns
        -------
        config
            Dict mapping section names to `{key: ConfigValue}` dicts,
            with `None` as the section of the top-level keys.
            Only flat (non-dict) values are included;
            nested subsections beyond one level are ignored,
            which matches the behavior of `patch_parser`.
        """
        config = self._literal_config()
        for section, patch in self._patches.items():
            if len(patch.values) > 0:
                config.setdefault(section, {}).update(patch.values)
        return config

    def _literal_config(self) -> dict[str | None, dict[str, ConfigValue]]:
        """Return what the config files hold, without coercion, merging or filtering.

        A key of a file with a higher priority hides the one of a file with a lower priority,
        because only one of the two can be shown on a line of its own.
        """
        config: dict[str | None, dict[str, ConfigValue]] = {}
        for config_file in self._configs:
            for key, value in config_file.data.items():
                if isinstance(value, dict):
                    settings = config.setdefault(key, {})
                    for sub_key, sub_value in value.items():
                        if not isinstance(sub_value, dict):
                            location = ConfigLocation.of_setting(config_file.path, key, sub_key)
                            settings[sub_key] = ConfigValue(sub_value, location)
                else:
                    location = ConfigLocation.of_setting(config_file.path, None, key)
                    config.setdefault(None, {})[key] = ConfigValue(value, location)
        return config

    #
    # Reporting problems
    #

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
        problems = list(self._errors)
        problems.extend(self._unknown_key_problems())
        problems.extend(self._unclaimed_section_problems())
        unique = {}
        for problem in problems:
            unique.setdefault(problem.message, problem)
        return list(unique.values())

    def _unknown_key_problems(self) -> list[ConfigProblem]:
        """Turn the keys that no parser has an argument for into problems."""
        # A table inside a section is never a setting, not even when the section
        # does have a setting of that name, which is what makes it a problem.
        problems = [self._unsupported_key_problem(location) for location in self._table_keys]
        for location in self._unknown_keys:
            if location.section is None and location.key in self._patches:
                # A section name that holds a scalar instead of a table.
                # The parser of that section already reported it as such.
                continue
            if location.section in self._sections_defining(location.key):
                # Another parser of the same section does have this argument,
                # and its value was applied. Two parsers share a section when one
                # subcommand is an alias of another.
                continue
            problems.append(self._unsupported_key_problem(location))
        return problems

    def _unsupported_key_problem(self, location: ConfigLocation) -> ConfigProblem:
        """Report a key that cannot configure anything, with a hint at what was meant."""
        hint = self._key_hint(location.path, location.key, location.section)
        phrase = self._section_phrase(location.path, location.section)
        return ConfigProblem(f"unsupported key {location.key!r} {phrase}{hint}", location)

    def _unclaimed_section_problems(self) -> list[ConfigProblem]:
        """Turn the sections that no parser claims into problems.

        Which sections these are is only known once every parser is patched,
        which is also why they cannot be detected in `patch_parser` itself.
        """
        sections = sorted(section for section in self._patches if section is not None)
        problems = []
        for config_file in self._configs:
            for key, value in config_file.data.items():
                if not isinstance(value, dict) or key in self._patches:
                    continue
                path = config_file.path
                section_path = self._section_path(path, key)
                owners = self._sections_defining(key)
                if len(owners) > 0:
                    # A setting written as a table, the counterpart of `_expected_section`.
                    detail = (
                        f"{_expected_value(section_path)} "
                        f"{self._join_section_phrases(path, owners)}"
                    )
                else:
                    matches = difflib.get_close_matches(key, sections)
                    detail = f"unknown section [{section_path}]{_hint([repr(m) for m in matches])}"
                problems.append(ConfigProblem(detail, ConfigLocation.of_section(path, key)))
        return problems

    def _key_hint(self, path: Path, key: str, section: str | None) -> str:
        """Suggest where an unsupported key belongs, or how it is spelled correctly.

        Parameters
        ----------
        path
            The config file the key was found in, which decides how a section is named.
        key
            The unsupported key.
        section
            The section the key was found in, `None` for the top level.

        Returns
        -------
        hint
            A suffix for the error message, empty when nothing plausible was found.
        """
        patch = self._patches.get(section)
        if patch is not None and key in patch.positional_dests:
            return " (a positional command-line argument, which cannot be configured)"
        owners = self._sections_defining(key) - {section}
        if len(owners) > 0:
            return f" (it belongs {self._join_section_phrases(path, owners)})"
        # A key equal to a supported one, without ending up in `owners`,
        # is a table where a setting is expected.
        # Suggesting its own name back is of no use.
        candidates = sorted(
            {dest for other in self._patches.values() for dest in other.actions} - {key}
        )
        suggestions = []
        for match in difflib.get_close_matches(key, candidates):
            match_owners = self._sections_defining(match)
            if section in match_owners:
                suggestions.append(repr(match))
            else:
                suggestions.append(f"{match!r} {self._join_section_phrases(path, match_owners)}")
        return _hint(suggestions)

    def _sections_defining(self, key: str) -> set[str | None]:
        """Return the sections in which `key` is a supported setting.

        Call this only after all `patch_parser` calls have been made.
        The result is empty for a key that no parser defines,
        and holds `None` when the key is a setting of the top-level parser.
        """
        return {section for section, patch in self._patches.items() if key in patch.actions}


def format_config_problems(problems: list[ConfigProblem], hint: str = "") -> str:
    """Combine the problems reported by `ConfigLoader.problems` into one error message.

    Parameters
    ----------
    problems
        The problems to report.
    hint
        A closing line suggesting what to do about the problems, omitted when empty.

    Returns
    -------
    text
        A multi-line message with one indented line per problem.
    """
    lines = [_PROBLEMS_HEADER]
    lines.extend(f"  {problem.message}" for problem in problems)
    if hint != "":
        lines.append(hint)
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
    text = Text(_PROBLEMS_HEADER)
    for problem in problems:
        text.append(f"\n  {problem.location}: ", style="bold")
        text.append(problem.detail, style=ERROR_STYLE)
    if hint != "":
        text.append(f"\n{hint}")
    print_error(text)
