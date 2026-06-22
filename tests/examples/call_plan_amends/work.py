#!/usr/bin/env python3
import glob

from stepup.core.api import amend, call
from stepup.core.call import driver


def plan():
    files = sorted(glob.glob("dir/*.txt"))
    amend(inp=files)
    call("./work.py", "run", inp=files, out=["result.txt"])


def run(inp, out):
    with open(out[0], "w") as f:
        for p in inp:
            with open(p) as g:
                f.write(g.read())


if __name__ == "__main__":
    driver()
