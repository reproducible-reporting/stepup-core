#!/usr/bin/env python3
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.dates import DateFormatter

from stepup.core.api import call
from stepup.core.call import driver


def plan(airport):
    call(
        "./plot.py",
        "run",
        inp=["matplotlibrc", f"{airport}.csv"],
        out=f"plot_{airport}.png",
        airport=airport,
    )


def run(inp, out, airport):
    mpl.rc_file(inp[0])
    dtype = [("dt", "datetime64[s]"), ("tmpc", "f8")]
    data = np.loadtxt(inp[1], dtype=dtype, delimiter=",", skiprows=1).T
    fig, ax = plt.subplots()
    ax.plot(data["dt"], data["tmpc"])
    ax.xaxis.set_major_formatter(DateFormatter("%d"))
    ax.set_xlabel("Day of the month February 2024")
    ax.set_xlim(data["dt"][0], data["dt"][-1])
    ax.set_ylabel("Temperature [°C]")
    ax.set_title(f"Airport {airport.upper()}")
    fig.savefig(out[0])


if __name__ == "__main__":
    driver()
