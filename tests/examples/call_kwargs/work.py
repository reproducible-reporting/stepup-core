#!/usr/bin/env python3
from stepup.core.call import driver


def run(inp, out, prefix: str):
    with open(out[0], "w", encoding="utf-8", newline="\n") as f:
        for path in inp:
            with open(path, encoding="utf-8") as g:
                f.write(f"{prefix} {g.read()}")


if __name__ == "__main__":
    driver()
