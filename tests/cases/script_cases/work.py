#!/usr/bin/env python

from helper import num_to_str

from stepup.core.script import driver

CASE_FMT = "{:+03.1f}"


def cases():
    yield -5.0
    yield 7.0


def case_info(num):
    return {
        "out": f"data{num:+03.1f}.txt",
        "num": num,
    }


def run(num, out):
    with open(out, "w") as fh:
        fh.write(f"{num_to_str(num)}\n")


if __name__ == "__main__":
    driver()
