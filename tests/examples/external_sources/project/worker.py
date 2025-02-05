#!/usr/bin/env python3
from helper import get_version

from stepup.core.call import driver


def run():
    return get_version()


if __name__ == "__main__":
    driver()
