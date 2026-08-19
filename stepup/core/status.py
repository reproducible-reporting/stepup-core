# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tool to get the current status directly from the graph database."""

import argparse
import sqlite3

from rich import print  # noqa: A004

from .config import ConfigLoader
from .enums import FileState, StepState
from .tool import SubParsers, ToolFunc, connect_graph_db

__all__ = ("add_status_subcommand",)

SQL_STEP_COUNTS = (
    "SELECT step.state, count(*) FROM node JOIN step ON node.i = step.node "
    "WHERE NOT node.detached GROUP BY step.state"
)

SQL_FILE_COUNTS = (
    "SELECT file.state, count(*) FROM node JOIN file ON node.i = file.node "
    "WHERE NOT node.detached GROUP BY file.state"
)

SQL_STEP_LABELS = (
    "SELECT label FROM node JOIN step ON node.i = step.node WHERE state = ? AND NOT detached"
)

# Resource units in use, without their available counterparts:
# the "available" half only lives in the director's in-memory `available_resource` temp table
# (seeded from `--resources`) and is never persisted to `graph.db`,
# so it cannot be reconstructed here.
SQL_RESOURCE_COUNTS = """
SELECT st.name, SUM(st.units) AS used
FROM step_resource AS st
JOIN step AS s ON s.node = st.node
WHERE s.state = ?
GROUP BY st.name
"""


def add_status_subcommand(subparsers: SubParsers, loader: ConfigLoader) -> ToolFunc:
    """Define command-line arguments for the status tool.

    Parameters
    ----------
    subparsers
        The subparser to add the status tool to.
    loader
        The configuration loader to override the default configuration with config file values.
        (Not used here.)

    Returns
    -------
    tool
       The function implementing the status subcommand.
    """
    subparsers.add_parser(
        "status",
        help="Print the status of the workflow, read directly from the graph database.",
    )
    return status_tool


def status_tool(args: argparse.Namespace) -> None:
    """Print the status of the workflow by reading the graph database directly."""
    con = connect_graph_db()
    try:
        print_status(con)
    finally:
        con.close()


def print_status(con: sqlite3.Connection):
    """Print the status of the workflow in the graph database behind the given connection."""
    print("[bold underline]Step counts[/]")
    for value, count in con.execute(SQL_STEP_COUNTS):
        print(f"  {StepState(value).name:10s} {count:6d}")
    print()

    print("[bold underline]File counts[/]")
    for value, count in con.execute(SQL_FILE_COUNTS):
        print(f"  {FileState(value).name:10s} {count:6d}")
    print()

    resource_counts = dict(con.execute(SQL_RESOURCE_COUNTS, (StepState.RUNNING.value,)))
    print("[bold underline]Resources[/]")
    if resource_counts:
        namelen = max(len(name) for name in resource_counts)
        for name, used in resource_counts.items():
            print(f"  {name:{namelen}s}  used {used:6d}")
    print()

    print("[bold underline]Running steps[/]")
    for state in (StepState.RUNNING, StepState.CHECKING):
        for (label,) in con.execute(SQL_STEP_LABELS, (state.value,)):
            print(f"  {label}")
