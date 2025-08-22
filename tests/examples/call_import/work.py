#!/usr/bin/env python3
from stepup.core.call import driver


def run():
    # Local import to test module dependency scanning.
    from helper import func  # noqa: PLC0415

    return func()


if __name__ == "__main__":
    driver()
