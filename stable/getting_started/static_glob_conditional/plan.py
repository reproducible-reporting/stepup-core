#!/usr/bin/env python3
from stepup.core.api import glob, static, step

if glob("dataset/"):
    static("dataset/bigfile.txt", "expensive.py")
    step(
        "./expensive.py",
        inp=["dataset/bigfile.txt", "expensive.py"],
        out="average.txt",
    )
else:
    static("average.txt")

step("cat average.txt", inp="average.txt")
