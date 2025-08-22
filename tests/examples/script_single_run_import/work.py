#!/usr/bin/env python3
from stepup.core.script import driver


def info():
    return {"out": "hello.txt"}


def run(out):
    # Local import to test module dependency scanning.
    from helper import func  # noqa: PLC0415

    with open(out, "w") as fh:
        print(func(), file=fh)


if __name__ == "__main__":
    driver()
