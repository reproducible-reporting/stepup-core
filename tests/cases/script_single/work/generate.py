#!/usr/bin/env python

import json

from stepup.core.script import driver


def info():
    return {
        "static": "config.json",
        "inp": "config.json",
        "out": "test.csv",
    }


def run():
    with open("config.json") as fh:
        config = json.load(fh)
    with open("test.csv", "w") as fh:
        fh.write("name,value\n")
        for value in config:
            if value % 2 == 0:
                fh.write(f"foo,{value}\n")
            else:
                fh.write(f"bar,{value}\n")


if __name__ == "__main__":
    driver()
