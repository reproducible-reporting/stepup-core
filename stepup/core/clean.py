# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright 2024-2026 Toon Verstraelen
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
"""Command-line interface to clean up output files and/or directories."""

import argparse
import os
import sqlite3

from path import Path
from rich.console import Console

from .cascade import DROP_CONSUMERS, INITIAL_CONSUMERS, RECURSE_CONSUMERS
from .enums import FileState
from .hash import FileHash
from .utils import mynormpath, sqlite3_copy_in_memory, translate, translate_back


def clean_subcommand(subparser: argparse.ArgumentParser) -> callable:
    """Define tool CLI options."""
    parser = subparser.add_parser("clean", help="Remove (stale) outputs in a directory. ")
    parser.add_argument(
        "paths",
        default=[Path("./")],
        type=Path,
        nargs="*",
        help="A list of paths to consider for the cleanup. "
        "Given a file, depending outputs will be cleaned. "
        "The file itself may also be removed. "
        "Given a directory, all containing outputs will be cleaned. "
        "The directory itself may also be removed. "
        "Unless additional flags are given, only old orphaned outputs are removed, "
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
        "Without this option, only old orphaned outputs are removed. "
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
    return clean_tool


def clean_tool(args: argparse.Namespace):
    """Main program."""
    # Translate all unique paths so they are relative to STEPUP_ROOT,
    # because this is how they are stored in the database. (tr_ prefix)
    # A trailing slash is appended to directories.
    tr_paths = set()
    for path in args.paths:
        lo_path = mynormpath(path)
        if lo_path.is_dir():
            lo_path /= ""
        tr_paths.add(translate(lo_path))

    # Copy the database in memory and work on the copy.
    root = Path(os.getenv("STEPUP_ROOT", "."))
    path_db = root / ".stepup/graph.db"
    with sqlite3_copy_in_memory(path_db) as con:
        clean(con, tr_paths, args)


def clean(con: sqlite3.Connection, tr_paths: set[str], args: argparse.Namespace):
    """Perform the cleanup of the given paths.

    Parameters
    ----------
    con
        The database connection.
    tr_paths
        The paths to consider for the cleanup.
    args
        The command-line arguments
    """
    # Find all related output paths
    tr_consuming_paths = search_consuming_paths(con, tr_paths, not args.all)
    tr_consuming_paths.sort(reverse=True)

    # Loop over paths, remove and collect info for stdout
    console = Console(highlight=False)
    if not args.commit:
        console.print("[yellow]# Note: No files or directories are actually removed.[/]")
        console.print("[yellow]# Use the --commit option to execute the removals.[/]")
    for tr_consuming_path, state, orphan, old_file_hash in tr_consuming_paths:
        # translate_back to local path
        lo_consuming_path = translate_back(tr_consuming_path)
        missing = not lo_consuming_path.exists()
        if missing:
            changed = False
        else:
            changed = (
                state != FileState.VOLATILE
                and old_file_hash.regen(lo_consuming_path) != old_file_hash
            )
            if args.safe and changed:
                console.print(
                    f"[grey]# Skipping modified file: {lo_consuming_path} "
                    "(use --unsafe to override)[/]"
                )
                continue

            # Remove if it is safe to do so
            if args.commit:
                if lo_consuming_path.endswith("/"):
                    lo_consuming_path.rmdir_p()
                else:
                    lo_consuming_path.remove_p()

        # Check removal
        still_there = args.commit and lo_consuming_path.exists()
        is_dir = lo_consuming_path.endswith("/")
        parts = [
            "# " if missing else "",
            "[green]rmdir[/] " if is_dir else "[cyan]rm[/] ",
            lo_consuming_path,
        ]
        if missing or still_there or changed or orphan:
            parts.append("  [bold red]#")
            if missing:
                parts.append(" Already gone!")
            if still_there:
                parts.append(" Removal failed!")
            if changed:
                parts.append(" File changed after workflow!")
            if orphan:
                parts.append(" Orphaned output!")
            parts.append("[/]")
        console.print("".join(parts))

    if not tr_consuming_paths:
        console.print("# No outputs found to be cleaned.")


def fmtnum(i: int):
    if i == 0:
        return "[grey]0[/]"
    return str(i)


CREATE_INITIAL_PATHS = "CREATE TABLE temp.initial_path(path TEXT PRIMARY KEY) WITHOUT ROWID"

SELECT_OUTPUTS = f"""
SELECT label, file.state, orphan, digest, mode, mtime, size, inode FROM node
JOIN all_consumer ON node.i = all_consumer.current
JOIN file ON file.node = all_consumer.current
WHERE file.state in
({FileState.BUILT.value}, {FileState.OUTDATED.value}, {FileState.VOLATILE.value})
"""

DROP_INITIAL_PATHS = "DROP TABLE IF EXISTS temp.initial_path"


def search_consuming_paths(
    con: sqlite3.Connection, initial_paths: list[Path], orphan_only: bool
) -> list[tuple[Path, FileState, bool, FileHash]]:
    """Find all paths that depend on the given initial paths.

    Parameters
    ----------
    con
        The database connection.
    initial_paths
        The initial paths to consider.

    Returns
    -------
    consuming_paths
        A list of paths and their file states and hashes.
        This only includes paths that are (volatile) outputs of steps.
        For each file, a tuple is returned with:
        - The path
        - The file state
        - The step state (of the step that created the file)
        - Whether it is orphaned
        - The file hash
    """
    try:
        con.execute(DROP_INITIAL_PATHS)
        con.execute(CREATE_INITIAL_PATHS)
        con.executemany(
            "INSERT INTO temp.initial_path VALUES(?)", ((path,) for path in initial_paths)
        )
        con.execute(DROP_CONSUMERS)
        con.execute(INITIAL_CONSUMERS)
        con.execute(
            "INSERT INTO temp.initial_consumer SELECT node.i AS current "
            "FROM node JOIN temp.initial_path ON node.label = temp.initial_path.path"
        )
        select_outputs = SELECT_OUTPUTS
        if orphan_only:
            select_outputs += " AND orphan"
        return [
            (row[0], FileState(row[1]), bool(row[2]), FileHash(*row[3:]))
            for row in con.execute(RECURSE_CONSUMERS + select_outputs)
        ]
    finally:
        con.execute(DROP_INITIAL_PATHS)
        con.execute(DROP_CONSUMERS)
