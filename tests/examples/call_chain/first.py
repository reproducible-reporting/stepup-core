#!/usr/bin/env python3
from stepup.core.api import call
from stepup.core.call import driver


def transform(inp, out):
    with (
        open(inp[0], encoding="utf-8", newline="\n") as g,
        open(out[0], "w", encoding="utf-8") as f,
    ):
        f.write(g.read().upper())
    call("./second.py", "finalize", inp=out, out=["final.txt"])


if __name__ == "__main__":
    driver()
