#!/usr/bin/env python3
from stepup.core.script import driver

CASE_FMT = "{}"


def cases():
    yield "first"
    yield "second"


def case_info(label):
    return {"out": f"{label}.txt", "label": label}


def run(out, label):
    from helper import func

    with open(out, "w") as fh:
        print(f"{label}: {func()}", file=fh)


if __name__ == "__main__":
    driver()
