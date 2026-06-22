#!/usr/bin/env python3
from stepup.core.call import callme, driver


@callme
def run(inp, out):
    with open(out[0], "w") as f:
        for path in inp:
            with open(path) as g:
                f.write(g.read())


if __name__ == "__main__":
    driver()
