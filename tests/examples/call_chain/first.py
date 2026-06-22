#!/usr/bin/env python3
from stepup.core.api import call
from stepup.core.call import callme, driver


@callme
def transform(inp, out):
    with open(inp[0]) as g, open(out[0], "w") as f:
        f.write(g.read().upper())
    call("./second.py", "finalize", inp=out, out=["final.txt"])


if __name__ == "__main__":
    driver()
