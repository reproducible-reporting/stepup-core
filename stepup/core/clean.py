# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Command-line interface to clean up output files and/or directories."""

import argparse
import os
import sqlite3
from collections.abc import Iterable

from path import Path
from rich.console import Console

from .config_loader import ConfigLoader
from .enums import FileState
from .hash import FileHash
from .path import translate, translate_back
from .sqlite3 import prefix_clause
from .tool import SubParsers, ToolFunc, connect_graph_db

__all__ = ("add_clean_subcommand",)


def add_clean_subcommand(subparsers: SubParsers, loader: ConfigLoader) -> ToolFunc:
    """Define command-line arguments for the clean tool.

    Parameters
    ----------
    subparsers
        The subparser to add the clean tool to.
    loader
        The configuration loader to override the default configuration with config file values.

    Returns
    -------
    tool_func
        The function to call with the parsed args to execute the clean command.
    """
    parser = subparsers.add_parser("clean", help="Remove (stale) outputs in a directory.")
    parser.add_argument(
        "paths",
        default=[Path(".")],
        type=Path,
        nargs="*",
        help="A list of paths to consider for the cleanup. "
        "Given a file, outputs depending on it will be cleaned. "
        "The file itself may also be removed. "
        "Given a directory, all outputs it contains will be cleaned. "
        "The directory itself may also be removed. "
        "Unless additional flags are given, only old detached outputs are removed, "
        "i.e. outputs for which there is no longer a corresponding step.",
    )
    parser.add_argument(
        "-c",
        "--commit",
        action="store_true",
        default=False,
        help="Execute the removal of files instead of only showing what would be done.",
    )
    parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        default=False,
        help="Remove outputs of any step in the workflow. "
        "Without this option, only old detached outputs are removed. "
        "Whenever a file is removed, also the outputs depending on it are removed.",
    )
    parser.add_argument(
        "-u",
        "--unsafe",
        action="store_false",
        default=True,
        dest="safe",
        help="Also remove output files that have been modified "
        "after their creation in the workflow.",
    )
    loader.patch_parser(parser)
    return clean_tool


def clean_tool(args: argparse.Namespace) -> None:
    """Clean up the outputs selected by the command-line arguments."""
    # Translate all unique paths so they are relative to STEPUP_ROOT,
    # because this is how they are stored in the database. (tr_ prefix)
    tr_paths = {translate(path.normpath()) for path in args.paths}

    # The cleanup removes files without changing the workflow, hence a read-only connection.
    con = connect_graph_db()
    try:
        clean(con, tr_paths, args)
    finally:
        con.close()


def clean(con: sqlite3.Connection, tr_paths: set[str], args: argparse.Namespace):
    """Perform the cleanup of the given paths.

    Parameters
    ----------
    con
        The database connection.
    tr_paths
        The paths to consider for the cleanup.
    args
        The command-line arguments.
    """
    # Find all paths matching the given paths
    tr_matching_paths = search_matching_paths(con, tr_paths)

    # Find all related output paths
    tr_consuming_paths = search_consuming_paths(con, tr_matching_paths, not args.all)
    tr_consuming_paths.sort(reverse=True)

    # Loop over the paths, remove them and collect information to print.
    console = Console(highlight=False)
    if not args.commit:
        console.print("[yellow]# Note: No files or directories are actually removed.[/]")
        console.print("[yellow]# Use the --commit option to execute the removals.[/]")
    parents = set()
    for tr_consuming_path, state, detached, old_file_hash in tr_consuming_paths:
        lo_consuming_path = translate_back(tr_consuming_path)
        missing = not lo_consuming_path.exists()
        if missing:
            changed = False
        else:
            changed = (
                state != FileState.VOLATILE
                and old_file_hash.refreshed(lo_consuming_path) != old_file_hash
            )
            if args.safe and changed:
                console.print(
                    f"[grey]# Skipping modified file: {lo_consuming_path} "
                    "(use --unsafe to override)[/]"
                )
                continue

            # Remove if it is safe to do so
            if args.commit:
                lo_consuming_path.remove_p()
                parents.add(lo_consuming_path.parent)

        # Check removal
        still_there = args.commit and lo_consuming_path.exists()
        parts = [
            "# " if missing else "",
            "[cyan]rm[/] ",
            lo_consuming_path,
        ]
        if missing or still_there or changed or detached:
            parts.append("  [bold red]#")
            if missing:
                parts.append(" Already gone!")
            if still_there:
                parts.append(" Removal failed!")
            if changed:
                parts.append(" File changed after the workflow created it!")
            if detached:
                parts.append(" Detached output!")
            parts.append("[/]")
        console.print("".join(parts))

    # Remove empty parent directories
    for parent in sorted(parents):
        while True:
            if parent.is_dir() and str(parent) not in (".", os.sep) and not any(parent.iterdir()):
                console.print(f"[cyan]rmdir[/] {parent}  [grey]# Empty parent directory[/]")
                if args.commit:
                    parent.rmdir()
                    parent = parent.parent
                else:
                    break
            else:
                break

    if not tr_consuming_paths:
        console.print("# No outputs found to be cleaned.")


SQL_MATCH_PATH = """
SELECT label FROM node JOIN file ON node.i = file.node
WHERE label = ? OR {clause}
"""

SQL_MATCH_ALL_PATHS = """
SELECT label FROM node JOIN file ON node.i = file.node
"""


def search_matching_paths(con: sqlite3.Connection, tr_paths: set[Path]) -> set[str]:
    """Find all paths that match the given paths.

    Parameters
    ----------
    con
        The database connection.
    tr_paths
        The paths to consider for the cleanup.

    Returns
    -------
    matching_paths
        A set of file paths matching the given paths,
        including all paths inside a given directory.
        Only file nodes are considered, never steps or other node kinds.
    """
    tr_matching_paths = set()
    for tr_path in tr_paths:
        if tr_path == ".":
            # Every file in the project is under the root, so no filter is needed.
            sql, args = SQL_MATCH_ALL_PATHS, ()
        else:
            clause, pattern = prefix_clause("label", tr_path / "")
            sql, args = SQL_MATCH_PATH.format(clause=clause), (tr_path, pattern)
        tr_matching_paths.update(row[0] for row in con.execute(sql, args))
    return tr_matching_paths


INITIAL_SINKS = "CREATE TABLE temp.initial_sink (current INTEGER PRIMARY KEY) WITHOUT ROWID"

RECURSE_SINKS_MULTI = """
WITH RECURSIVE all_sink(current) AS (
    -- Initial: Select the initial nodes
    SELECT current
    FROM temp.initial_sink
    UNION
    -- Recursion: Follow edges by selecting sinks of current
    SELECT sink AS current
    FROM dependency INNER JOIN all_sink ON source = current
)
"""

SELECT_OUTPUTS = f"""
SELECT label, file.state, detached, hash
FROM all_sink
JOIN node ON node.i = all_sink.current
JOIN file ON file.node = all_sink.current
WHERE file.state in
({FileState.BUILT.value}, {FileState.OUTDATED.value}, {FileState.VOLATILE.value})
"""

DROP_SINKS = "DROP TABLE IF EXISTS temp.initial_sink"


def search_consuming_paths(
    con: sqlite3.Connection, initial_paths: Iterable[Path], detached_only: bool
) -> list[tuple[Path, FileState, bool, FileHash]]:
    """Find all paths that depend on the given initial paths.

    Parameters
    ----------
    con
        The database connection.
    initial_paths
        The initial paths to consider.
        They are included in the results themselves,
        subject to the same filtering as the paths depending on them.
    detached_only
        When True only detached (volatile) output paths are searched for.

    Returns
    -------
    consuming_paths
        A list of paths and their file states and hashes.
        This only includes paths that are (volatile) outputs of steps.
        For each file, a tuple is returned with:

        - The path
        - The file state
        - Whether it is detached
        - The file hash
    """
    try:
        con.execute(DROP_SINKS)
        con.execute(INITIAL_SINKS)
        con.executemany(
            "INSERT INTO temp.initial_sink SELECT node.i FROM node WHERE node.label = ?",
            ((path,) for path in initial_paths),
        )
        select_outputs = SELECT_OUTPUTS
        if detached_only:
            select_outputs += " AND detached"
        return [
            (Path(row[0]), FileState(row[1]), bool(row[2]), FileHash.from_json(row[3]))
            for row in con.execute(RECURSE_SINKS_MULTI + select_outputs)
        ]
    finally:
        con.execute(DROP_SINKS)
