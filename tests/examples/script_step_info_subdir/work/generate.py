#!/usr/bin/env python3

import json

from stepup.core.script import driver


def info():
    return {"inp": "config.json", "out": "values.txt"}


def run():
    with open("config.json", encoding="utf-8") as fh:
        config = json.load(fh)
    with open("values.txt", "w", encoding="utf-8", newline="\n") as fh:
        for value in config:
            fh.write(f"{value}\n")


if __name__ == "__main__":
    driver()
