#!/usr/bin/env python3

from stepup.core.script import driver


def info():
    return {
        "out": "runout.txt",
    }


def run():
    with open("runout.txt", "w", encoding="utf-8", newline="\n") as fh:
        print("Some intermediate output.", file=fh)


if __name__ == "__main__":
    driver()
