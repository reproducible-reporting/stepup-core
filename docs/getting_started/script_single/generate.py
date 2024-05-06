#!/usr/bin/env python
import json

import numpy as np

from stepup.core.script import driver


def info():
    return {
        "inp": "config.json",
        "out": ["cos.npy", "sin.npy"],
    }


def run(inp, out):
    with open("config.json") as fh:
        config = json.load(fh)
    nstep = config["nstep"]
    freq = config["freq"]
    np.save(out[0], np.cos(2 * np.pi * freq * np.arange(nstep)))
    np.save(out[1], np.sin(2 * np.pi * freq * np.arange(nstep)))


if __name__ == "__main__":
    driver()
