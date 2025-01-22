#!/usr/bin/env python3
from stepup.core.call import driver


def run(text: str) -> str:
    return text[::-1]


if __name__ == "__main__":
    driver()
