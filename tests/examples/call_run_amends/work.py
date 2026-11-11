#!/usr/bin/env python3
from stepup.core.api import amend
from stepup.core.call import driver


def run(inp):
    with open(inp[0], encoding="utf-8") as g:
        lines = g.read().splitlines()
    for i, line in enumerate(lines):
        path = f"out_{i}.txt"
        amend(out=[path])
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")


if __name__ == "__main__":
    driver()
