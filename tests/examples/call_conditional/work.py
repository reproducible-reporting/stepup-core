#!/usr/bin/env python3
from stepup.core.api import call
from stepup.core.call import callme, driver


@callme
def plan(inp):
    with open(inp[0]) as f:
        if f.read().strip() == "enabled":
            call("./work.py", "run", out=["result.txt"])


@callme
def run(out):
    with open(out[0], "w") as f:
        f.write("done\n")


if __name__ == "__main__":
    driver()
