#!/usr/bin/env python3
from stepup.core.api import call, static

static("generate.py", "plot.py", "matplotlibrc")

for case_r in [2.2, 2.8, 3.2, 3.8]:
    call("./generate.py", "run", out=f"logmap_{case_r:5.3f}.txt", r=case_r, optional=True)

call(
    "./plot.py",
    "run",
    inp=["matplotlibrc", "logmap_3.200.txt"],
    out="plot_logmap.png",
)
