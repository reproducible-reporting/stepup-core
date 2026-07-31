#!/usr/bin/env python3
from stepup.core.api import glob, run, static

if glob("dataset/bigfile.txt"):
    static("dataset/bigfile.txt", "expensive.py")
    run("./expensive.py", inp="dataset/bigfile.txt", out="average.txt")
else:
    static("average.txt")

run("cat average.txt", inp="average.txt")
