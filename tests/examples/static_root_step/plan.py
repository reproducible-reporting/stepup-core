#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("dir_inp")
runsh(
    "cp ../dir_inp/inp.txt ../dir_out/out.txt; cp ../dir_inp/inp.txt ../dir_vol/vol.txt",
    workdir="dir_work",
    inp="../dir_inp/inp.txt",
    out="../dir_out/out.txt",
    vol="../dir_vol/vol.txt",
)
