#!/usr/bin/env python3
from stepup.core.api import glob, runsh, static

glob("dir_*/", _defer=True)
# The runsh is a bit artificial to test different code paths.
runsh(
    "cp ../dir_inp/inp.txt ../dir_out/out.txt; cp ../dir_inp/inp.txt ../dir_vol/vol.txt",
    workdir="dir_work",
    inp="../dir_inp/inp.txt",
    out="../dir_out/out.txt",
    vol="../dir_vol/vol.txt",
)
static("dir_inp/inp.txt")
