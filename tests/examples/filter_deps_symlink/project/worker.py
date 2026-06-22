#!/usr/bin/env python3
import json

from helper import get_version


def run():
    with open("worker_out.json", "w") as f:
        json.dump(get_version(), f)


if __name__ == "__main__":
    run()
