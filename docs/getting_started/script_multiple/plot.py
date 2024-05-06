#!/usr/bin/env python
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.dates import DateFormatter

from stepup.core.script import driver


def cases():
    yield "ebbr"
    yield "ebos"


CASE_FMT = "plot_{}"


def case_info(airport):
    return {
        "inp": ["matplotlibrc", f"{airport}.csv"],
        "out": f"plot_{airport}.png",
        "airport": airport,
    }


def run(inp, out, airport):
    matplotlib.rc_file(inp[0])
    dtype = [("dt", "datetime64[s]"), ("tmpc", "f8")]
    data = np.loadtxt(inp[1], dtype=dtype, delimiter=",", skiprows=1).T
    fig, ax = plt.subplots()
    ax.plot(data["dt"], data["tmpc"])
    ax.xaxis.set_major_formatter(DateFormatter("%d"))
    ax.set_xlabel("Day of the month February 2024")
    ax.set_xlim(data["dt"][0], data["dt"][-1])
    ax.set_ylabel("Temperature [°C]")
    ax.set_title(f"Airport {airport.upper()}")
    fig.savefig(out)


if __name__ == "__main__":
    driver()
