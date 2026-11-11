#!/usr/bin/env python3
from stepup.core.call import driver


def run(inp, out, factor: int):
    with open(inp[0], encoding="utf-8") as g:
        content = g.read()
    with open(out[0], "w", encoding="utf-8", newline="\n") as f:
        f.write(content * factor)


if __name__ == "__main__":
    driver()
