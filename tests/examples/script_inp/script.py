#!/usr/bin/env python3
from stepup.core.script import driver

CASE_FMT = "{}"


def cases():
    with open("config.txt") as fh:
        for line in fh:
            line = line.strip()
            if len(line) > 0:
                yield line


def case_info(line):
    return {"out": f"out_{line}.txt"}


def run(out):
    with open(out, "w") as fh:
        fh.write(f"Hello, {out}!")


if __name__ == "__main__":
    driver()
