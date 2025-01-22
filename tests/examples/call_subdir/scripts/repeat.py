#!/usr/bin/env python3
from stepup.core.call import driver


def run(inp, out, n):
    with open(inp[0]) as fh:
        data = fh.read()
    with open(out[0], "w") as fh:
        for _ in range(n):
            fh.write(data)


if __name__ == "__main__":
    driver()
