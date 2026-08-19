# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""StepUp Command Line Interface."""

import argparse
import os
import sys
from collections.abc import Callable
from importlib.metadata import entry_points
from importlib.metadata import version as get_version

from path import Path

from .config import (
    ConfigLoader,
    format_config_problems,
    print_config_error,
    print_config_problems,
)
from .enums import ReturnCode
from .exceptions import ConfigError
from .utils import is_debug

__all__ = ("main", "sb_main")


SHOW_CONFIG = "show-config"
"""The subcommand that runs despite a broken configuration, because it reports what is wrong."""


def main():
    """Run a StepUp subcommand, reporting a `ConfigError` as a message instead of a traceback.

    `STEPUP_DEBUG` keeps the traceback, as it does for any other usage error.
    """
    try:
        _main()
    except ConfigError as exc:
        if is_debug():
            raise
        print_config_error(str(exc))
        sys.exit(ReturnCode.INTERNAL.value)


def _main():
    """Parse the command line and run the requested subcommand if the configuration allows it."""
    parser, tool_funcs, loader = build_parser()
    args = parser.parse_args()
    tool_func = tool_funcs.get(args.tool)
    if tool_func is None:
        parser.print_help()
        return
    if args.tool != SHOW_CONFIG:
        problems = loader.problems()
        if len(problems) > 0:
            hint = f"Run 'stepup {SHOW_CONFIG}' to inspect the configuration."
            if is_debug():
                raise ConfigError(f"{format_config_problems(problems)}\n{hint}")
            print_config_problems(problems, hint)
            sys.exit(ReturnCode.INTERNAL.value)
    sys.exit(tool_func(args))


def sb_main():
    """Shortcut for `stepup build` (accepts the same arguments)."""
    sys.argv.insert(1, "build")
    main()


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, Callable], ConfigLoader]:
    """Create the StepUp parser, with all subcommands registered and configured.

    Returns
    -------
    parser
        The top-level argument parser.
    tool_funcs
        The function implementing each subcommand, keyed by subcommand name.
    loader
        The configuration loader,
        patched into the top-level parser and passed to every subcommand.
        Call `ConfigLoader.check` on it to find out what is wrong with the configuration.
    """
    # Configuration loader
    stepup_root = Path(os.getenv("STEPUP_ROOT", os.getcwd()))
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
    parser.add_argument("--version", "-V", action="version", version="%(prog)s " + version)
    debug = is_debug()
    parser.add_argument(
        "--log-level",
        "-l",
        default=os.getenv("STEPUP_LOG_LEVEL", "DEBUG" if debug else "WARNING").upper(),
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level. [default=%(default)s]",
    )
    loader.patch_parser(parser, use_section=False)

    # Load tool entry points
    subparsers = parser.add_subparsers(dest="tool", required=False)
    tool_eps = sorted(entry_points(group="stepup.tools"), key=lambda ep: ep.name)
    tool_funcs = {}
    for tool_ep in tool_eps:
        tool = tool_ep.load()
        tool_funcs[tool_ep.name] = tool(subparsers, loader)

    return parser, tool_funcs, loader


if __name__ == "__main__":
    main()
