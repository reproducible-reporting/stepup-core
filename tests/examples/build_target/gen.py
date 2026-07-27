#!/usr/bin/env python3
from stepup.core.api import copy, static
from stepup.core.call import driver


def plan():
    # Only declared once this planning step actually runs, to demonstrate that a target
    # does not need to be discoverable upfront.
    static("seed.txt")
    copy("seed.txt", "dynamic.txt")


if __name__ == "__main__":
    driver()
