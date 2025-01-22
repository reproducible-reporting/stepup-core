#!/usr/bin/env python3
from stepup.core.call import driver


def run():
    from helper import func

    return func()


if __name__ == "__main__":
    driver()
