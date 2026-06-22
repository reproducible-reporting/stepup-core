#!/usr/bin/env python3
from stepup.core.call import callme, driver


@callme
def run(inp, out):
    with open(inp[0]) as g, open(out[0], "w") as f:
        f.write(g.read())


if __name__ == "__main__":
    driver()
