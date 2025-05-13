#!/usr/bin/env python3
"""StepUp plan to generate all the outputs of the examples."""

from path import Path

from stepup.core.api import glob, runpy, static


def scan_main(path_main: str) -> tuple[list[Path], Path, list[Path]]:
    """Get list of static inputs, StepUp root and some outputs of the example.

    This information is embedded in the comments of `main.sh` and the commands

    ```
    stepup boot -n 1 | sed -f ../../clean_stdout.sed > stdout.txt
    # INP: input
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
    inp = []
    workdir = Path(path_main).parent
    root = workdir
    out = []
    with open(path_main) as fh:
        for line in fh:
            if line.startswith("# INP:"):
                inp.append(workdir / line[6:].strip())
            elif line.startswith("# ROOT:"):
                root = workdir / line[7:].strip()
                inp.append(root)
            elif "stepup " in line and " > " in line:
                out.append(workdir / Path(line[line.find(">") + 1 :].strip()))
            elif line.startswith("# OUT:"):
                out.append(workdir / line[6:].strip())
    return inp, root, out


def main():
    """Main program."""
    static("run_example.py", "getting_started/", "advanced_topics/")
    glob("getting_started/*/")
    glob("advanced_topics/*/")
    paths_main = glob("*/*/main.sh")
    for path_main in paths_main:
        inp, root, out = scan_main(path_main)
        static(inp)
        inp.append(Path("run_example.py"))
        inp.append(path_main)
        runpy(f"./run_example.py {path_main} {root}", inp=inp, out=out)


if __name__ == "__main__":
    main()
