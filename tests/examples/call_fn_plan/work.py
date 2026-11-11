#!/usr/bin/env python3
from stepup.core.api import call
from stepup.core.call import driver


def plan(inp):
    call("./work.py", "run", inp=inp, out=["result.txt"])


def run(inp, out):
    with open(out[0], "w", encoding="utf-8", newline="\n") as f:
        for path in inp:
            with open(path, encoding="utf-8") as g:
                f.write(g.read())


if __name__ == "__main__":
    driver()
