#!/usr/bin/env python3
"""Run one example and amend (volatile) outputs."""

import sqlite3
import subprocess
import sys

from path import Path

from stepup.core.api import amend
from stepup.core.file import FileState

SQL = "SELECT label, state FROM node JOIN file ON node.i = file.node"


def main():
    path_main, root = sys.argv[1:]

    workdir, name_main = Path(path_main).splitpath()
    subprocess.run(f"./{name_main}", cwd=workdir, check=False)

    root = Path(root)
    path_stepup = root / ".stepup/"
    path_graph = path_stepup / "graph.db"
    con = sqlite3.Connection(f"file:{path_graph}?mode=ro", uri=True)
    out = [path_stepup]
    vol = [path_graph]
    for path, state_i in con.execute(SQL):
        state = FileState(state_i)
        if state == FileState.BUILT:
            out.append(root / path)
        elif state == FileState.VOLATILE:
            vol.append(root / path)
    amend(out=out, vol=vol)


if __name__ == "__main__":
    main()
