#!/usr/bin/env python3
import glob

from stepup.core.api import amend, call
from stepup.core.call import driver


def plan():
    files = sorted(glob.glob("input_*.txt"))
    amend(inp=files)
    for path in files:
        stem = path[len("input_") : -4]
        call("./work.py", "run", inp=[path], out=[f"output_{stem}.txt"])


def run(inp, out):
    with open(inp[0]) as g, open(out[0], "w") as f:
        f.write(g.read().upper())


if __name__ == "__main__":
    driver()
