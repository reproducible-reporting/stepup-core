#!/usr/bin/env python3
from stepup.core.api import glob, runsh, static

if glob("data.txt"):
    runsh("grep -i foo data.txt > analyzed.txt", inp=["data.txt"], out=["analyzed.txt"])
else:
    static("analyzed.txt")
runsh("cat analyzed.txt", inp=["analyzed.txt"])
