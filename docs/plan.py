#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""StepUp plan to generate all the outputs of the examples."""

import re

from path import Path

from stepup.core.api import run, shq, static

# A command that runs StepUp, possibly after variable assignments or inside a subshell,
# e.g. `MYVAR=foo sb -j 1` or `(cd source; sb -j 1)`.
RE_BUILD = re.compile(r"(?:^|[(;|&])\s*(?:\w+=\S*\s+)*(?:sb|stepup\s+build)\s")


def scan_main(path_main: str) -> tuple[list[Path], Path, list[Path]]:
    """Get list of static inputs, StepUp root and some outputs of the example.

    This information is embedded in the comments of `main.sh` and the commands

    ```bash
    sb -j 1 | sed -f ../../clean_stdout.sed > stdout.txt
    # INP: input
    # OUT: output (for outputs of other commands)
    # ROOT: root/ (optional)
    ```

    Parameters
    ----------
    path_main
        The path of the `main.sh` script.

    Returns
    -------
    inp
        A list of static input paths.
    root
        The root of the StepUp example run.
    out
        A list of output paths, deduced from output redirection.
    """
    inp = [Path("clean_stdout.sed")]
    workdir = Path(path_main).parent
    root = workdir
    out = []
    with open(path_main, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("# INP:"):
                inp.append(workdir / line[6:].strip())
            elif line.startswith("# ROOT:"):
                root = workdir / line[7:].strip()
            elif line.startswith("# OUT:"):
                out.append(workdir / line[6:].strip())
            elif RE_BUILD.search(line) and " > " in line:
                out.append(workdir / line.rsplit(" > ", 1)[1].strip())
    return inp, root, sorted(set(out))


def main():
    """Main program."""
    static("run_example.py")
    static("clean_stdout.sed")
    paths_main = static("*/*/main.sh")
    for path_main in paths_main:
        inp, root, out = scan_main(path_main)
        static(inp)
        inp.append(path_main)
        run(f"./run_example.py {shq(path_main)} {shq(root)}", inp=inp, out=out)


if __name__ == "__main__":
    main()
