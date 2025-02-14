#!/usr/bin/env python3
from stepup.core.script import driver


def info():
    return {
        "inp": "inp1.txt",
        "out": "out1.txt",
    }


def run(inp, out):
    with open(inp) as fh:
        text = fh.read()
    with open(out, "w") as fh:
        fh.write(text)


if __name__ == "__main__":
    driver()
