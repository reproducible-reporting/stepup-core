#!/usr/bin/env python3
import time

from stepup.core.call import callme, driver


@callme
def run(out):
    with open(out[0], "w") as f:
        f.write("stable\n")
    with open("volatile.txt", "w") as f:
        f.write(str(time.time()) + "\n")


if __name__ == "__main__":
    driver()
