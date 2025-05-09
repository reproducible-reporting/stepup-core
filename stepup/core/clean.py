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
"""Command-line interface to clean up output files and/or directories."""

import argparse
import os
import sqlite3
from collections import Counter

from path import Path
from rich.console import Console
from rich.table import Table

from .cascade import DROP_CONSUMERS, INITIAL_CONSUMERS, RECURSE_CONSUMERS
from .enums import FileState
from .hash import FileHash
from .utils import mynormpath, translate, translate_back


def clean_subcommand(subparser: argparse.ArgumentParser) -> callable:
    """Define tool CLI options."""
    parser = subparser.add_parser(
        "clean",
        help="Recursively remove outputs of a file or in a directory. "
        "This script follows the dependency graph only, not the provenance graph, to find outputs.",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="A list of paths to consider for the cleanup. "
        "Given a file, all depending outputs will be cleaned. "
        "The file itself is also removed if it is not static. "
        "Given a directory, all containing outputs will be removed. "
        "The directory itself is also removed if it is not static. "
        "The cleanup is performed recursively: outputs of outputs are also removed, etc.",
    )
    parser.add_argument(
        "-d",
        "--dry-run",
        action="store_true",
        help="Do not remove any files, just show what would be done.",
    )
    return clean_tool


def clean_tool(args: argparse.Namespace):
    """Main program."""
    # Get all unique paths relative to STEPUP_ROOT, possible amended with a trailing slash.
    tr_paths = set()
    for path in args.paths:
        lo_path = mynormpath(path)
        if lo_path.is_dir():
            lo_path /= ""
        tr_paths.add(translate(lo_path))

    # Copy the database in memory, close it and work on the copy.
    root = Path(os.getenv("STEPUP_ROOT", "."))
    path_db = root / ".stepup/graph.db"
    dst = sqlite3.Connection(":memory:")
    try:
        src = sqlite3.Connection(path_db)
        try:
            src.backup(dst)
        finally:
            src.close()
        clean(dst, tr_paths, args)
    finally:
        dst.close()


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
    tr_consuming_paths = search_consuming_paths(con, tr_paths)
    tr_consuming_paths.sort(reverse=True)

    # Loop over paths, remove and collect info for stdout
    counter = Counter()
    colors0 = {"?": "yellow", "!": "red", " ": "green"}
    colors1 = {"d": "cyan", "f": "blue"}
    console = Console(highlight=False)
    for tr_consuming_path, state, old_file_hash in tr_consuming_paths:
        # translate_back to local path
        lo_consuming_path = translate_back(tr_consuming_path)
        label = "" if lo_consuming_path.exists() else "?"

        # Remove if it is safe
        if not args.dry_run and (
            state == FileState.VOLATILE or old_file_hash.regen(lo_consuming_path) == old_file_hash
        ):
            if lo_consuming_path.endswith("/"):
                lo_consuming_path.rmdir_p()
            else:
                lo_consuming_path.remove_p()

        # Check removal
        if label == "":
            label += "!" if lo_consuming_path.exists() else " "
        label += "d" if lo_consuming_path.endswith("/") else "f"
        c0 = colors0[label[0]]
        c1 = colors1[label[1]]
        console.print(f"[bold {c0}]{label[0]}[/][bold {c1}]{label[1]}[/] {lo_consuming_path}")
        counter[label] += 1
    console.print()

    # Print summary
    title = "Cleanup summary"
    if args.dry_run:
        title += " (dry run)"
    table = Table(title=title)
    table.add_column("Category")
    table.add_column("[bold blue]Files[/]", justify="right")
    table.add_column("[bold cyan]Dirs[/]", justify="right")
    table.add_row("[bold yellow]Not found[/]", fmtnum(counter["?f"]), fmtnum(counter["?d"]))
    table.add_row("[bold red]Not removed[/]", fmtnum(counter["!f"]), fmtnum(counter["!d"]))
    table.add_row("Removed", fmtnum(counter[" f"]), fmtnum(counter[" d"]))
    console.print(table)


def fmtnum(i: int):
    if i == 0:
        return "[grey]0[/]"
    return str(i)


INITIAL_PATHS = "CREATE TABLE temp.initial_path(path TEXT PRIMARY KEY) WITHOUT ROWID"

SELECT_OUTPUTS = f"""
-- Final: Get all (indirect) outputs
SELECT label, state, digest, mode, mtime, size, inode FROM node
JOIN all_consumer ON node.i = all_consumer.current
JOIN file ON file.node = all_consumer.current
WHERE file.state in
({FileState.BUILT.value}, {FileState.OUTDATED.value}, {FileState.VOLATILE.value})
AND NOT orphan
"""

DROP_PATHS = "DROP TABLE IF EXISTS temp.initial_path"


def search_consuming_paths(
    con: sqlite3.Connection, initial_paths: list[Path]
) -> list[tuple[Path, FileState, FileHash]]:
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
        This only includes paths that are (volatile) outputs of steps,
    """
    try:
        con.execute(DROP_PATHS)
        con.execute(INITIAL_PATHS)
        con.executemany(
            "INSERT INTO temp.initial_path VALUES(?)", ((path,) for path in initial_paths)
        )
        con.execute(DROP_CONSUMERS)
        con.execute(INITIAL_CONSUMERS)
        con.execute(
            "INSERT INTO temp.initial_consumer SELECT node.i AS current "
            "FROM node JOIN temp.initial_path ON node.label = temp.initial_path.path"
        )
        return [
            (row[0], FileState(row[1]), FileHash(*row[2:]))
            for row in con.execute(RECURSE_CONSUMERS + SELECT_OUTPUTS)
        ]
    finally:
        con.execute(DROP_PATHS)
        con.execute(DROP_CONSUMERS)
