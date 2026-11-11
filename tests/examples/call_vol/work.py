#!/usr/bin/env python3
import time

from stepup.core.call import driver


def run(out):
    with open(out[0], "w", encoding="utf-8", newline="\n") as f:
        f.write("stable\n")
    with open("volatile.txt", "w", encoding="utf-8", newline="\n") as f:
        f.write(str(time.time()) + "\n")


if __name__ == "__main__":
    driver()
