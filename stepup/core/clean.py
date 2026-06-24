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
from .config import ConfigLoader
from .constants import GRAPH_DB
from .enums import FileState
from .hash import FileHash
from .sqlite3 import copy_db_in_memory, escape_like_pattern
from .utils import translate, translate_back


def clean_subcommand(subparsers, loader: ConfigLoader) -> callable:
    """Define command-line arguments for the clean tool.

    Parameters
    ----------
    subparsers
        The sub parser to add the clean tool to.
    loader
        The configuration loader to override the default configuration with
        config file values.
    """
    parser = subparsers.add_parser("clean", help="Remove (stale) outputs in a directory. ")
    parser.add_argument(
        "paths",
        default=[Path(".")],
        type=Path,
        nargs="*",
        help="A list of paths to consider for the cleanup. "
        "Given a file, depending outputs will be cleaned. "
        "The file itself may also be removed. "
        "Given a directory, all containing outputs will be cleaned. "
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
    loader.patch_parser(parser, "clean")
    return clean_tool


def clean_tool(args: argparse.Namespace):
    """Main program."""
    # Translate all unique paths so they are relative to STEPUP_ROOT,
    # because this is how they are stored in the database. (tr_ prefix)
    # A trailing slash is appended to directories.
    tr_paths = {translate(path.normpath()) for path in args.paths}

    # Copy the database in memory and work on the copy.
    root = Path(os.getenv("STEPUP_ROOT", "."))
    path_db = root / GRAPH_DB
    with copy_db_in_memory(path_db) as con:
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
    # Find all paths matching the given paths
    tr_matching_paths = search_matching_paths(con, tr_paths)

    # Find all related output paths
    tr_consuming_paths = search_consuming_paths(con, tr_matching_paths, not args.all)
    tr_consuming_paths.sort(reverse=True)

    # Loop over paths, remove and collect info for stdout
    console = Console(highlight=False)
    if not args.commit:
        console.print("[yellow]# Note: No files or directories are actually removed.[/]")
        console.print("[yellow]# Use the --commit option to execute the removals.[/]")
    parents = set()
    for tr_consuming_path, state, detached, old_file_hash in tr_consuming_paths:
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
                parts.append(" File changed after workflow!")
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


def fmtnum(i: int):
    if i == 0:
        return "[grey]0[/]"
    return str(i)


SQL_MATCH_PATH = """
SELECT label FROM node JOIN file ON node.i = file.node
WHERE label = ? OR label LIKE ? ESCAPE '\\'
"""


def search_matching_paths(con: sqlite3.Connection, tr_paths: set[str]) -> set[str]:
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
        A set of paths that match the given paths.
        This only includes paths that are (volatile) outputs of steps.
    """
    tr_matching_paths = set()
    for tr_path in tr_paths:
        pattern = "%" if tr_path == "." else escape_like_pattern(tr_path / "") + "%"
        tr_matching_paths.update(row[0] for row in con.execute(SQL_MATCH_PATH, (tr_path, pattern)))
    return tr_matching_paths


SELECT_OUTPUTS = f"""
SELECT label, file.state, detached, digest, mode, mtime, size, inode
FROM all_consumer
JOIN node ON node.i = all_consumer.current
JOIN file ON file.node = all_consumer.current
WHERE file.state in
({FileState.BUILT.value}, {FileState.OUTDATED.value}, {FileState.VOLATILE.value})
"""


def search_consuming_paths(
    con: sqlite3.Connection, initial_paths: list[Path], detached_only: bool
) -> list[tuple[Path, FileState, bool, FileHash]]:
    """Find all paths that depend on the given initial paths.

    Parameters
    ----------
    con
        The database connection.
    initial_paths
        The initial paths to consider.
        They will be included in the returned results.

    Returns
    -------
    consuming_paths
        A list of paths and their file states and hashes.
        This only includes paths that are (volatile) outputs of steps.
        For each file, a tuple is returned with:
        - The path
        - The file state
        - The step state (of the step that created the file)
        - Whether it is detached
        - The file hash
    """
    try:
        con.execute(DROP_CONSUMERS)
        con.execute(INITIAL_CONSUMERS)
        con.executemany(
            "INSERT INTO temp.initial_consumer SELECT node.i FROM node WHERE node.label = ?",
            ((path,) for path in initial_paths),
        )
        select_outputs = SELECT_OUTPUTS
        if detached_only:
            select_outputs += " AND detached"
        return [
            (row[0], FileState(row[1]), bool(row[2]), FileHash(*row[3:]))
            for row in con.execute(RECURSE_CONSUMERS + select_outputs)
        ]
    finally:
        con.execute(DROP_CONSUMERS)
