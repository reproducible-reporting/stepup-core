#!/usr/bin/env python3
from path import Path

from stepup.core.call import driver


def run(repeat, inp, out):
    parts = []
    for path_inp in inp:
        assert isinstance(path_inp, Path)
        with open(path_inp) as fh:
            parts.append(fh.read() * repeat)
    for path_out in out:
        assert isinstance(path_out, Path)
        with open(path_out, "w") as fh:
            fh.write("".join(parts))


if __name__ == "__main__":
    driver()
