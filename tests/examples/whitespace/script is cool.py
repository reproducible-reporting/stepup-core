#!/usr/bin/env python3
from stepup.core.script import driver


def info():
    return {"inp": "README NOW.txt", "out": "the road to hell is paved with whitespace.txt"}


def run(inp, out):
    with open(inp) as fh:
        text = fh.read()
    with open(out, "w") as fh:
        fh.write(text)


if __name__ == "__main__":
    driver()
