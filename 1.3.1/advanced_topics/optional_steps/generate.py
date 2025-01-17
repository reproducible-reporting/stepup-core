#!/usr/bin/env python
from stepup.core.script import driver


def cases():
    yield 2.2
    yield 2.8
    yield 3.2
    yield 3.8


CASE_FMT = "logmap_{:5.3f}"


def case_info(r):
    return {"out": f"logmap_{r:5.3f}.txt", "r": r}


def run(out, r):
    x = 0.1
    with open(out, "w") as fh:
        for _ in range(100):
            print(f"{x:10.5f}", file=fh)
            x = r * x * (1 - x)


if __name__ == "__main__":
    driver()
