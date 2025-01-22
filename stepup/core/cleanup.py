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
"""Command-line interface to manually clean up output files and/or directories."""

import argparse
import os
import sqlite3
from collections import Counter

from path import Path

from .cascade import DROP_CONSUMERS, INITIAL_CONSUMERS, RECURSE_CONSUMERS
from .enums import FileState
from .hash import FileHash
from .utils import mynormpath, translate


def main():
    """Main program."""
    args = parse_args()

    # Get all unique paths relative to STEPUP_ROOT, possible amended with a trailing slash.
    tr_paths = set()
    for path in args.paths:
        lo_path = mynormpath(path)
        if lo_path.is_dir():
            lo_path /= ""
        tr_paths.add(translate(lo_path))

    # Open the database in read-only mode
    root = Path(os.getenv("STEPUP_ROOT", "."))
    path_db = root / ".stepup/graph.db"
    con = sqlite3.Connection(f"file:{path_db}?mode=ro", uri=True)

    # Find all related output paths
    tr_consuming_paths = search_consuming_paths(con, tr_paths)
    tr_consuming_paths.sort(reverse=True)

    # Loop over paths, remove and collect info for stdout
    counter = Counter()
    for tr_consuming_path, file_hash in tr_consuming_paths:
        # Translate back to local path
        lo_consuming_path = translate.back(tr_consuming_path)
        if args.verbose >= 1:
            label = "" if lo_consuming_path.exists() else "?"

        # Remove if hash has not changed
        if file_hash.update(lo_consuming_path) is False:
            if lo_consuming_path.endswith("/"):
                lo_consuming_path.rmdir_p()
            else:
                lo_consuming_path.remove_p()

        # Check removal
        if args.verbose >= 1:
            if label == "":
                label += "!" if lo_consuming_path.exists() else " "
            label += "d" if lo_consuming_path.endswith("/") else "f"
            if args.verbose >= 2:
                print(f"{label} {lo_consuming_path}")
            counter[label] += 1

    # Summary
    if args.verbose >= 1:
        print("                Files      Dirs")
        print(f"Not found:   {counter['?f']:8d}  {counter['?d']:8d}")
        print(f"Not removed: {counter['!f']:8d}  {counter['!d']:8d}")
        print(f"Removed:     {counter[' f']:8d}  {counter[' d']:8d}")


INITIAL_PATHS = "CREATE TABLE temp.initial_path(path TEXT PRIMARY KEY) WITHOUT ROWID"

SELECT_OUTPUTS = """
-- Final: Get all (indirect) outputs
SELECT label, digest, mode, mtime, size, inode FROM node
JOIN all_consumer ON node.i = all_consumer.current
JOIN file ON file.node = all_consumer.current
WHERE file.state in (:built, :volatile, :outdated) AND NOT orphan
"""

DROP_PATHS = "DROP TABLE IF EXISTS temp.initial_path"


def search_consuming_paths(
    con: sqlite3.Connection, initial_paths: list[Path]
) -> list[tuple[Path, FileHash]]:
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
        all_paths = [
            (row[0], FileHash(*row[1:]))
            for row in con.execute(
                RECURSE_CONSUMERS + SELECT_OUTPUTS,
                {
                    "built": FileState.BUILT.value,
                    "volatile": FileState.VOLATILE.value,
                    "outdated": FileState.OUTDATED.value,
                },
            )
        ]
    finally:
        con.execute(DROP_PATHS)
        con.execute(DROP_CONSUMERS)
    return all_paths


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="cleanup",
        description="Recursively remove outputs of a file or in a directory.\n\n"
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
    parser.add_argument("-v", "--verbose", action="count", default=0)
    return parser.parse_args()


if __name__ == "__main__":
    main()
