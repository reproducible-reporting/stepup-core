#!/usr/bin/env python3
from stepup.core.call import driver


def finalize(inp, out):
    with open(inp[0]) as g, open(out[0], "w") as f:
        f.write(g.read().strip() + " done\n")


if __name__ == "__main__":
    driver()
