#!/usr/bin/env python3
from stepup.core.call import callme, driver


@callme
def run(out):
    with open(out[0], "w") as f:
        f.write("done\n")


if __name__ == "__main__":
    driver()
