#!/usr/bin/env python3
from stepup.core.call import driver


def run(inp, out):
    with open(inp[0], encoding="utf-8") as g:
        content = g.read()
    for path in out:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)


if __name__ == "__main__":
    driver()
