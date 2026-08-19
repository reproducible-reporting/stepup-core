# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""StepUp Command Line Interface."""

import argparse
import sys
from importlib.metadata import entry_points
from importlib.metadata import version as get_version

from .config import (
    ConfigLoader,
    format_config_problems,
    print_config_problems,
)
from .enums import ReturnCode
from .exceptions import ConfigError, UsageError
from .path import get_stepup_root
from .tool import print_error
from .utils import is_debug

__all__ = ("main", "sb_main")


#
# Console scripts
#


def sb_main():
    """Shortcut for `stepup build` (accepts the same arguments).

    Top-level options are not accepted, because `build` is inserted before all arguments,
    so they must be given to the `stepup` command instead, e.g. `stepup --version`.
    """
    sys.argv.insert(1, "build")
    main()


def main():
    """Run a StepUp subcommand and decide the exit status from the exception that reaches here.

    A `UsageError` is a mistake the user can fix, so it is reported as a short message.
    Any other exception keeps its traceback, because it points at a bug in StepUp.
    `STEPUP_DEBUG` turns the first case into the second one,
    which is also the only way to see where a usage error was raised.
    A `SystemExit` passes through untouched:
    a tool raises it to end the process with an exit code of its own,
    e.g. the bit flags that `stepup build` reports.
    """
    try:
        _run_subcommand()
    except UsageError as exc:
        if is_debug():
            raise
        print_error(str(exc))
        sys.exit(ReturnCode.INTERNAL.value)
    except KeyboardInterrupt:
        if is_debug():
            raise
        print_error("Interrupted.")
        sys.exit(ReturnCode.INTERRUPTED.value)


#
# Internals
#


def _run_subcommand():
    """Parse the command line and run the requested subcommand if the configuration allows it."""
    parser, loader = _setup_cli()
    args = parser.parse_args()
    if args.tool is None:
        parser.print_help()
        sys.exit(ReturnCode.INTERNAL.value)
    # The config tool is exempt: it is the tool that explains a broken configuration.
    if args.tool != "config":
        _exit_on_config_problems(loader)
    args.tool_func(args)


def _setup_cli() -> tuple[argparse.ArgumentParser, ConfigLoader]:
    """Create the StepUp parser, with all subcommands registered and configured.

    Returns
    -------
    parser
        The top-level argument parser.
        Subcommands are registered as subparsers on this parser,
        and are alphabetically sorted by name.
        Parsing a subcommand puts its `ToolFunc` in the `tool_func` attribute of the arguments.
    loader
        The configuration loader,
        patched into the top-level parser and passed to every subcommand.
    """
    # Configuration loader
    stepup_root = get_stepup_root()
    loader = ConfigLoader(
        prefix="stepup",
        config_paths=[
            "/etc/stepup.toml",
            "~/.config/stepup.toml",
            stepup_root / ".stepup.toml",
            stepup_root / "stepup.toml",
            stepup_root / "pyproject.toml",
            stepup_root / "stepup-local.toml",
        ],
    )

    # Base argument parser
    parser = argparse.ArgumentParser(
        prog="stepup",
        description="General purpose dynamic build tool.",
    )
    version = get_version("stepup")
    parser.add_argument("-V", "--version", action="version", version="%(prog)s " + version)
    loader.patch_parser(parser, use_section=False)

    # Load tool entry points
    subparsers = parser.add_subparsers(dest="tool", required=False)
    tool_eps = sorted(entry_points(group="stepup.tools"), key=lambda ep: ep.name)
    for tool_ep in tool_eps:
        add_subcommand = tool_ep.load()
        tool_func = add_subcommand(subparsers, loader)
        tool_parser = subparsers.choices.get(tool_ep.name)
        if tool_parser is None:
            raise RuntimeError(
                f"The entry point '{tool_ep.name} = {tool_ep.value}' "
                f"did not add a subparser named '{tool_ep.name}'."
            )
        tool_parser.set_defaults(tool_func=tool_func)

    return parser, loader


def _exit_on_config_problems(loader: ConfigLoader) -> None:
    """Report the problems of the configuration and exit, if there are any.

    Raises
    ------
    ConfigError
        With `STEPUP_DEBUG`, instead of printing the problems, to get a traceback.
    """
    problems = loader.problems()
    if len(problems) > 0:
        hint = "Run 'stepup config' to inspect the configuration."
        if is_debug():
            raise ConfigError(f"{format_config_problems(problems)}\n{hint}")
        print_config_problems(problems, hint)
        sys.exit(ReturnCode.INTERNAL.value)


if __name__ == "__main__":
    main()
