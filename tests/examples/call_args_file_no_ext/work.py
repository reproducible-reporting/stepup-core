#!/usr/bin/env python3
from stepup.core.call import driver


def run(out):
    with open(out[0], "w", encoding="utf-8", newline="\n") as f:
        f.write("done\n")


if __name__ == "__main__":
    driver()
