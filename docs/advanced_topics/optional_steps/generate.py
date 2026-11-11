#!/usr/bin/env python3
from stepup.core.call import driver


def run(out: list[str], r: float):
    x = 0.1
    with open(out[0], "w", encoding="utf-8", newline="\n") as fh:
        for _ in range(100):
            print(f"{x:10.5f}", file=fh)
            x = r * x * (1 - x)


if __name__ == "__main__":
    driver()
