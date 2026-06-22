#!/usr/bin/env python3
import os

from stepup.core.call import callme, driver


@callme
def run(out):
    with open(out[0], "w") as f:
        f.write(os.environ.get("MY_STEP_VALUE", "unset") + "\n")


if __name__ == "__main__":
    driver()
