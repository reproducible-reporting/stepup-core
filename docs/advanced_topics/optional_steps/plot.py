#!/usr/bin/env python
import matplotlib
import matplotlib.pyplot as plt
import numpy as np

from stepup.core.script import driver


def info():
    r = 3.2
    return {
        "inp": ["matplotlibrc", f"logmap_{r:5.3f}.txt"],
        "out": "plot_logmap.png",
    }


def run(inp, out):
    matplotlib.rc_file(inp[0])
    seq = np.loadtxt(inp[1])
    fig, ax = plt.subplots()
    ax.plot(seq)
    ax.set_xlabel("n")
    ax.set_ylabel("x_n")
    fig.savefig(out)


if __name__ == "__main__":
    driver()
