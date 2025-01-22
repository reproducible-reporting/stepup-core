#!/usr/bin/env python3
from stepup.core.api import amend
from stepup.core.call import driver


def run(out: str):
    amend(out="check.txt")
    with open("check.txt", "w") as fh:
        fh.write(out)


if __name__ == "__main__":
    driver()
