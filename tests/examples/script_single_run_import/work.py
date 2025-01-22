#!/usr/bin/env python3
from stepup.core.script import driver


def info():
    return {"out": "hello.txt"}


def run(out):
    from helper import func

    with open(out, "w") as fh:
        print(func(), file=fh)


if __name__ == "__main__":
    driver()
