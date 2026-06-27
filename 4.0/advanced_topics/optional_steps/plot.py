#!/usr/bin/env python3
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np

from stepup.core.call import driver


def run(inp: list[str], out: list[str]):
    mpl.rc_file(inp[0])
    seq = np.loadtxt(inp[1])
    fig, ax = plt.subplots()
    ax.plot(seq)
    ax.set_xlabel("n")
    ax.set_ylabel("x_n")
    fig.savefig(out[0])


if __name__ == "__main__":
    driver()
