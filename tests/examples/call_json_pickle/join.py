#!/usr/bin/env python3
from stepup.core.call import driver


def run(word1: str, word2: str):
    return {"out": f"{word1} {word2}"}


if __name__ == "__main__":
    driver()
