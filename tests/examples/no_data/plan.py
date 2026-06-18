#!/usr/bin/env python3
from stepup.core.api import glob, run, static

if glob("data.txt"):
    run("grep -i foo data.txt > analyzed.txt", shell=True, inp=["data.txt"], out=["analyzed.txt"])
else:
    static("analyzed.txt")
run("cat analyzed.txt", inp=["analyzed.txt"])
