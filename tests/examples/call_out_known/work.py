#!/usr/bin/env python3
from stepup.core.call import callme, driver


@callme
def run(inp, out):
    with open(inp[0]) as g:
        content = g.read()
    for path in out:
        with open(path, "w") as f:
            f.write(content)


if __name__ == "__main__":
    driver()
