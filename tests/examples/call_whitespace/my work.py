#!/usr/bin/env python3
from stepup.core.call import driver


def run(inp, out):
    with (
        open(inp[0], encoding="utf-8", newline="\n") as g,
        open(out[0], "w", encoding="utf-8") as f,
    ):
        f.write(g.read())


if __name__ == "__main__":
    driver()
