# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Shared infrastructure for the subcommands of the `stepup` command line interface.

A subcommand is registered through a `stepup.tools` entry point named after the subcommand,
which points at a function `add_<name>_subcommand(subparsers, loader) -> ToolFunc`.
It adds a parser named `<name>` to `subparsers` and returns the `ToolFunc` that runs the
subcommand with the arguments that parser produces.
A subcommand with settings that a config file or an environment variable may decide
also calls `ConfigLoader.patch_parser` on its parser.
One without such settings ignores `loader`,
which keeps its section out of the configuration altogether.
"""

import argparse
import sqlite3
from collections.abc import Callable

from path import Path
from rich.console import Console
from rich.style import Style
from rich.text import Text

from .constants import GRAPH_DB
from .exceptions import ToolError
from .path import get_stepup_root
from .sqlite3 import connect

__all__ = (
    "ERROR_STYLE",
    "SubParsers",
    "ToolFunc",
    "connect_graph_db",
    "get_graph_db_path",
    "print_error",
)


# Python does not offer a public interface for the return type of `ArgumentParser.add_subparsers`.
SubParsers = argparse._SubParsersAction
"""The registry of subcommand parsers, as returned by `ArgumentParser.add_subparsers`."""

ToolFunc = Callable[[argparse.Namespace], None]
"""The implementation of a StepUp subcommand: it takes the parsed arguments and returns nothing.

A mistake that the user can fix is raised as a `UsageError`, usually a `ToolError`,
which `stepup` turns into a short message on standard error.
A tool that must end the process with a specific exit code calls `sys.exit`.
"""

ERROR_STYLE = Style(color="red", bold=True, dim=False)
"""How an error stands out from the rest of the output.

`dim=False` is needed because a problem shown inline by `config` sits in a TOML comment,
whose syntax highlighting is dim.
"""


def print_error(message: str | Text) -> None:
    """Print an error on standard error, in color where the terminal allows it.

    Parameters
    ----------
    message
        The error, without the `ERROR:` prefix, which is added here.
        A `Text` instance is printed with its own styling on top of the prefix.
    """
    # Soft wrapping leaves long messages to the terminal instead of cropping them.
    console = Console(stderr=True, soft_wrap=True)
    console.print(Text.assemble(("ERROR:", ERROR_STYLE), " ", message))


def get_graph_db_path() -> Path:
    """Locate the workflow database of the project.

    Returns
    -------
    path_db
        The path of `GRAPH_DB` under `STEPUP_ROOT`.

    Raises
    ------
    ToolError
        If the database does not exist,
        which is the normal situation in a directory where StepUp has not run yet.
    """
    path_db = get_stepup_root() / GRAPH_DB
    if not path_db.exists():
        raise ToolError(f"Graph database {path_db} does not exist.")
    return path_db


def connect_graph_db() -> sqlite3.Connection:
    """Open the workflow database of the project read-only.

    Returns
    -------
    con
        A read-only connection, to be closed by the caller.

    Raises
    ------
    ToolError
        If the database does not exist.
    """
    return connect(get_graph_db_path(), read_only=True)
