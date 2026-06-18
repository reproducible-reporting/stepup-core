#!/usr/bin/env python3
from stepup.core.api import run, static

static("dir_inp")
run(
    "cp ../dir_inp/inp.txt ../dir_out/out.txt; cp ../dir_inp/inp.txt ../dir_vol/vol.txt",
    shell=True,
    workdir="dir_work",
    inp="../dir_inp/inp.txt",
    out="../dir_out/out.txt",
    vol="../dir_vol/vol.txt",
)
