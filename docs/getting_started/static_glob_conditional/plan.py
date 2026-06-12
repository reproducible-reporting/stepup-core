#!/usr/bin/env python3
from stepup.core.api import glob, runsh, static

if glob("dataset/bigfile.txt"):
    static("expensive.py")
    runsh(
        "./expensive.py",
        inp=["dataset/bigfile.txt", "expensive.py"],
        out="average.txt",
    )
else:
    static("average.txt")

runsh("cat average.txt", inp="average.txt")
