#!/usr/bin/env python3
from math import pi, sin

from stepup.core.call import driver


def run(freq: float, duration: float, rate: int = 44100):
    """Generate a sine wave with given frequency, duration, and sample rate."""
    return [sin(2 * pi * freq * i / rate) for i in range(int(duration * rate))]


if __name__ == "__main__":
    driver()
