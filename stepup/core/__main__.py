# StepUp Core provides the basic framework for the StepUp build tool.
# © 2024–2025 Toon Verstraelen
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
"""StepUp Command Line Interface."""

import argparse
import os
from importlib.metadata import entry_points
from importlib.metadata import version as get_version

from path import Path

from .utils import string_to_bool


def main():
    parser, tool_funcs = build_parser()
    args = parser.parse_args()
    tool_func = tool_funcs.get(args.tool)
    if tool_func is not None:
        tool_func(args)
    else:
        parser.print_help()


def build_parser() -> tuple[argparse.ArgumentParser, list[callable]]:
    # Base argument parser
    parser = argparse.ArgumentParser(
        prog="stepup",
        description="General purpose dynamic build tool.",
    )
    version = get_version("stepup")
    parser.add_argument("--version", "-V", action="version", version="%(prog)s " + version)
    debug = string_to_bool(os.getenv("STEPUP_DEBUG", "0"))
    parser.add_argument(
        "--log-level",
        "-l",
        default=os.getenv("STEPUP_LOG_LEVEL", "DEBUG" if debug else "WARNING").upper(),
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level. [default=%(default)s]",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(os.getenv("STEPUP_ROOT", os.getcwd())),
        help="Directory containing top-level plan.py [default=%(default)s]",
    )

    # Load tool entry points
    subparsers = parser.add_subparsers(dest="tool", required=False)
    tool_eps = sorted(entry_points(group="stepup.tools"), key=lambda ep: ep.name)
    tool_funcs = {}
    for tool_ep in tool_eps:
        tool = tool_ep.load()
        tool_funcs[tool_ep.name] = tool(subparsers)

    return parser, tool_funcs


if __name__ == "__main__":
    main()
