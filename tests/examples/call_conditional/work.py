#!/usr/bin/env python3
from stepup.core.api import call
from stepup.core.call import driver


def plan(inp):
    with open(inp[0], encoding="utf-8") as f:
        if f.read().strip() == "enabled":
            call("./work.py", "run", out=["result.txt"])


def run(out):
    with open(out[0], "w", encoding="utf-8", newline="\n") as f:
        f.write("done\n")


if __name__ == "__main__":
    driver()
