#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
# """Run one example and amend (volatile) outputs."""

import sys

from stepup.core.api import amend
from stepup.core.enums import FileState
from stepup.core.extapi import run_subprocess
from stepup.core.graph.sqlite3 import connect
from stepup.core.path import Path

SQL = "SELECT label, state FROM node JOIN file ON node.i = file.node"


def main():
    path_main, root = sys.argv[1:]

    workdir, name_main = Path(path_main).splitpath()
    root = Path(root)
    try:
        run_subprocess(f"./{name_main}", workdir=workdir)
        out, vol = collect_outputs(root)
    except Exception:
        # The exit status of a `stepup build` piped through `sed` in `main.sh` is lost,
        # so a build that stopped early only shows up here, e.g. as a graph without tables.
        # Its own output tells why it stopped.
        print_build_output(workdir, root)
        raise
    amend(out=out, vol=vol)


def collect_outputs(root: Path) -> tuple[list[Path], list[Path]]:
    """Find the built and volatile files in the workflow graph of the example.

    Returns
    -------
    out
        The files built by the example.
    vol
        The volatile files of the example, including the workflow graph itself.
    """
    path_graph = root / ".stepup/" / "graph.db"
    con = connect(path_graph, read_only=True)
    out = []
    vol = [path_graph]
    try:
        for path, state_i in con.execute(SQL):
            state = FileState(state_i)
            if state == FileState.BUILT:
                out.append(root / path)
            elif state == FileState.VOLATILE:
                vol.append(root / path)
    finally:
        con.close()
    return out, vol


def print_build_output(workdir: Path, root: Path):
    """Print the terminal output and the director log of the builds in the example."""
    paths = sorted(workdir.glob("stdout*.txt"))
    paths.append(root / ".stepup/director.log")
    for path in paths:
        print(f"==== {path} ====", file=sys.stderr)
        if path.is_file():
            print(path.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
        else:
            print("(missing)", file=sys.stderr)


if __name__ == "__main__":
    main()
