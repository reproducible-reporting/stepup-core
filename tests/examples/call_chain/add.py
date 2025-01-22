#!/usr/bin/env python3
from stepup.core.call import driver


def run(a, b):
    return {"x": a + b}


if __name__ == "__main__":
    driver()
