#!/usr/bin/env python3
from stepup.core.call import callme, driver


@callme
def plan(inp: list[str], config: str = "default"):
    pass


@callme
def run(inp: list[str], out: list[str]):
    pass


if __name__ == "__main__":
    driver()
