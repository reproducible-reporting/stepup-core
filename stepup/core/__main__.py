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

from .config import ConfigLoader
from .utils import is_debug

__all__ = ("main", "sb_main")


def main():
    parser, tool_funcs = build_parser()
    args = parser.parse_args()
    tool_func = tool_funcs.get(args.tool)
    if tool_func is not None:
        sys.exit(tool_func(args))
    else:
        parser.print_help()


def sb_main():
    """Shortcut for `stepup build` (accepts the same arguments)."""
    sys.argv.insert(1, "build")
    main()


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, Callable]]:
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

    return parser, tool_funcs


if __name__ == "__main__":
    main()
