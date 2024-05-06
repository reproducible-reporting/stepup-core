#!/usr/bin/env python
"""StepUp plan to generate all the outputs of the examples."""

from path import Path

from stepup.core.api import amend, glob, static, step


def scan_main(path_main: str) -> tuple[list[Path], Path, list[Path]]:
    """Get list of static inputs, StepUp root and some outputs of the example.

    This information is embedded in the comments of `main.sh` and the commands

    ```
    stepup -n -w 1 | sed -f ../../clean_stdout.sed > stdout.txt
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
            elif line.startswith("stepup") and ">" in line:
                out.append(workdir / Path(line[line.find(">") + 1 :].strip()))
    return inp, root, out


def main():
    static("run_example.py", "getting_started/", "advanced_topics/")
    glob("getting_started/*/")
    glob("advanced_topics/*/")
    paths_main = glob("*/*/main.sh")
    assert amend(inp=paths_main.files())
    for path_main in paths_main:
        inp, root, out = scan_main(path_main)
        static(inp)
        inp.append(Path("run_example.py"))
        inp.append(path_main)
        step(f"./run_example.py {path_main} {root}", inp=inp, out=out)


if __name__ == "__main__":
    main()
