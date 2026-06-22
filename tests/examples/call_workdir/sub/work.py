#!/usr/bin/env python3
from stepup.core.call import callme, driver


@callme
def run(inp, out):
    print("Hello from work.py!")
    with open(out[0], "w") as f, open(inp[0]) as g:
        f.write(g.read())


if __name__ == "__main__":
    driver()
