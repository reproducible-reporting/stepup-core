# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""The `stepup config` subcommand, which renders the effective configuration as TOML.

The output is valid TOML on standard output,
so it stays usable in a pipeline even when the configuration is broken.
Every setting carries a trailing comment naming the config file or environment variable
it came from, and the config files and environment variables themselves are listed as comments.

## Where a Problem Is Shown

This is the one tool that renders the problems of `ConfigLoader.problems` itself,
because showing a problem on the line of the setting it concerns is what the tool is for.
`_render_config` records every line a problem could concern as an `_Anchor`,
keyed by the `ConfigLocation` that the line shows.
`_inline_problems` then looks up the location of each problem
and appends it to its own line, inside a comment.
A problem for which no line was found, for instance one about a setting that a config file
with a higher priority overrides, has nothing to be appended to
and is reported on standard error instead.

This module is a plain client of the public `ConfigLoader` API in `config_loader.py`.
"""

import argparse
import sys
from collections.abc import Iterable
from itertools import accumulate
from typing import Any

import attrs
from rich.console import Console
from rich.syntax import Syntax

from .config_loader import (
    ConfigLoader,
    ConfigLocation,
    ConfigProblem,
    ConfigValue,
    print_config_problems,
)
from .constants import CORE_ENV_VARS, INTERNAL_ENV_VARS
from .enums import ReturnCode
from .path import short_path
from .tool import ERROR_STYLE, SubParsers, ToolFunc

__all__ = ("add_config_subcommand",)


_ERROR_MARK = "<-- ERROR: "
"""What sets a problem apart from the comment naming the source of a setting."""

_PROBLEM_SEP = "  "
"""The gap between a rendered line and the problem shown on it, which stays unstyled."""


#
# Value objects
#


@attrs.define(frozen=True)
class _Anchor:
    """A line in the rendered configuration that a problem can be shown on."""

    index: int = attrs.field()
    """The position of the line in the rendered output."""

    is_section_header: bool = attrs.field(default=False)
    """Whether the line is a `[section]` header, which decides how a problem is shown on it.

    Such a line carries no comment yet, so a problem must open one to keep the output
    valid TOML, and it names no config file, so the problem must name its own source.
    Every other line a problem can land on already ends in a comment that names its source.
    """

    def mark(self, problem: ConfigProblem) -> str:
        """Render `problem` as it is shown on this line, without the gap that precedes it."""
        text = problem.message if self.is_section_header else problem.detail
        return f"{'# ' if self.is_section_header else ''}{_ERROR_MARK}{text}"


@attrs.define(frozen=True)
class _Span:
    """The part of a rendered line that holds a problem, to be styled as an error.

    A line holds one problem at most, appended at its end,
    so the problem always runs to the end of the line and no stop column is stored.
    """

    index: int = attrs.field()
    """The position of the line the problem sits on."""

    start: int = attrs.field()
    """The column the problem starts at."""


@attrs.define
class _Report:
    """The rendered configuration under construction, with the places a problem can go.

    The line index of an anchor is only ever assigned by `add`,
    so a caller never has to work out where the line it appends will end up.
    """

    lines: list[str] = attrs.field(factory=list)
    """The rendered TOML, without line separators."""

    anchors: dict[ConfigLocation, _Anchor] = attrs.field(factory=dict)
    """Every place a problem can be shown at, keyed by the location the line shows."""

    def add(
        self,
        line: str,
        locations: Iterable[ConfigLocation] = (),
        *,
        is_section_header: bool = False,
    ) -> None:
        """Append one line, anchoring to it every location it shows.

        Parameters
        ----------
        locations
            The locations that a problem shown on this line can concern,
            empty for a line that no problem can be shown on.
        is_section_header
            Whether the line is a `[section]` header, see `_Anchor`.
        """
        for location in locations:
            self.anchors[location] = _Anchor(len(self.lines), is_section_header)
        self.lines.append(line)

    def show(self, anchor: _Anchor, problem: ConfigProblem) -> _Span:
        """Append `problem` to the anchored line and return the span it occupies there."""
        line = self.lines[anchor.index]
        self.lines[anchor.index] = line + _PROBLEM_SEP + anchor.mark(problem)
        return _Span(anchor.index, len(line) + len(_PROBLEM_SEP))


#
# Entry point
#


def add_config_subcommand(subparsers: SubParsers, loader: ConfigLoader) -> ToolFunc:
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
        report = _render_config(loader)
        spans, remaining = _inline_problems(report, problems)
        _print_toml(report.lines, spans)
        if len(remaining) > 0:
            print_config_problems(remaining)
        if len(problems) > 0:
            sys.exit(ReturnCode.INTERNAL.value)

    return config_tool


#
# Rendering
#


def _render_config(loader: ConfigLoader) -> _Report:
    """Render the merged configuration as TOML, without the problems.

    Parameters
    ----------
    loader
        The configuration loader, with every parser patched into it.

    Returns
    -------
    report
        The rendered TOML and every place a problem can be shown at.
    """
    report = _Report()

    report.add("# Config files (lowest to highest priority):")
    for config_file in loader.config_files:
        tag = "FOUND:  " if config_file.exists else "MISSING:"
        location = ConfigLocation.of_file(config_file.path)
        report.add(f"#   {tag} {short_path(config_file.path)}", [location])
    report.add(f"# Environment variables: {loader.env_prefix}*")
    n_header_lines = len(report.lines)

    config = loader.effective_config()
    env_vars = loader.prefixed_env_vars()
    top_settings = config.get(None, {})
    sections = {k: v for k, v in config.items() if k is not None and v}

    if top_settings:
        report.add("")
        _add_settings(report, top_settings)

    for section in sorted(sections):
        report.add("")
        # A section header is rendered once for the merged configuration,
        # while a problem about a section names the config file it was found in,
        # so every config file is given an anchor on this one line.
        locations = [
            ConfigLocation.of_section(config_file.path, section)
            for config_file in loader.config_files
        ]
        report.add(f"[{section}]", locations, is_section_header=True)
        _add_settings(report, sections[section])

    _add_env_vars(report, env_vars, loader.recognized_env_vars())

    # Whether anything was rendered is read back from the report,
    # instead of being worked out in advance from what the three cases above would add.
    if len(report.lines) == n_header_lines:
        report.add("")
        report.add("# No configuration found.")

    return report


def _add_settings(report: _Report, settings: dict[str, ConfigValue]) -> None:
    """Append the settings of one section, sorted by key, each naming where it came from.

    Parameters
    ----------
    report
        The rendered configuration, extended in place.
    settings
        The settings to render, keyed by key,
        as one section of `ConfigLoader.effective_config`.
    """
    for key in sorted(settings):
        entry = settings[key]
        # A setting decided by an environment variable gets no anchor:
        # a problem about that variable is shown on the line listing the variable itself,
        # which is the only line that names it.
        locations = [] if entry.location.env_var is not None else [entry.location]
        report.add(f"{key} = {_toml_value(entry.value)}  # {entry.location}", locations)


def _add_env_vars(report: _Report, env_vars: dict[str, str], recognized: set[str]) -> None:
    """Append the environment variables of the prefix as comments, grouped by what they do.

    Parameters
    ----------
    report
        The rendered configuration, extended in place.
    env_vars
        The environment variables to list, keyed by name.
    recognized
        The names that the patched parsers recognize, as returned by
        `ConfigLoader.recognized_env_vars`.
    """
    setting_vars = []
    core_vars = []
    internal_vars = []
    unrecognized_vars = []
    for env_var in sorted(env_vars):
        if env_var in recognized:
            setting_vars.append(env_var)
        elif env_var in CORE_ENV_VARS:
            core_vars.append(env_var)
        elif env_var in INTERNAL_ENV_VARS:
            internal_vars.append(env_var)
        else:
            unrecognized_vars.append(env_var)
    groups = [
        ("# Configuration environment variables:", setting_vars),
        ("# StepUp Core module environment variables:", core_vars),
        (
            "# Internal environment variables, overruled by StepUp (probably a mistake):",
            internal_vars,
        ),
        ("# Unrecognized environment variables, without effect:", unrecognized_vars),
    ]
    for header, group in groups:
        if len(group) == 0:
            continue
        report.add("")
        report.add(header)
        for env_var in group:
            value = _toml_value(env_vars[env_var])
            report.add(f"#   {env_var} = {value}", [ConfigLocation.of_env(env_var)])


#
# Inlining the problems
#


def _inline_problems(
    report: _Report, problems: list[ConfigProblem]
) -> tuple[list[_Span], list[ConfigProblem]]:
    """Append every problem that has a line of its own to that line.

    Parameters
    ----------
    report
        The rendered configuration, amended in place.
    problems
        The problems to show.

    Returns
    -------
    spans
        The part of a line that each appended problem occupies.
    remaining
        The problems that no line of their own was found for.
    """
    spans = []
    remaining = []
    taken: set[int] = set()
    for problem in problems:
        # A setting has no anchor when another source overrides it,
        # because the rendered line then shows that other source and not this problem's.
        anchor = report.anchors.get(problem.location)
        # A line holds one problem at most.
        # A second one would have to reopen the comment that the first one already sits in,
        # so it is reported below the configuration instead.
        if anchor is None or anchor.index in taken:
            remaining.append(problem)
            continue
        taken.add(anchor.index)
        spans.append(report.show(anchor, problem))
    return spans, remaining


#
# Output
#


def _print_toml(lines: list[str], spans: list[_Span]) -> None:
    """Print TOML lines with syntax highlighting, with the given spans marked as problems.

    Parameters
    ----------
    lines
        The lines to print, without line separators.
    spans
        The parts of the lines to mark as problems,
        each running to the end of the line it sits on.
    """
    toml_text = "\n".join(lines) + "\n"
    # Highlighting into a `Text` instead of printing a `Syntax` allows the problems
    # to be styled on top of the syntax highlighting.
    text = Syntax(toml_text, "toml", theme="ansi_dark", word_wrap=False).highlight(toml_text)
    line_starts = list(accumulate((len(line) + 1 for line in lines), initial=0))
    for span in spans:
        start = line_starts[span.index]
        text.stylize(ERROR_STYLE, start + span.start, start + len(lines[span.index]))
    Console(soft_wrap=True).print(text)


def _toml_value(value: Any) -> str:
    """Format a Python value as a TOML literal.

    A value of a type that TOML has no literal for is rendered as its string form,
    because a string is what the `type` callable of the corresponding argparse action
    accepts back, so the rendered line stays a valid way to configure the setting.
    A `Decimal` reaches this case, and its string form is the only one that survives
    the round trip, since TOML would turn `3.0` into a float
    and `Decimal(3.0)` is `Decimal("3")`.
    """
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
    return _toml_value(str(value))
