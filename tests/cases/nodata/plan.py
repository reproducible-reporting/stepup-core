#!/usr/bin/env python
from stepup.core.api import glob, static, step

if glob("data.txt"):
    step("grep -i foo data.txt > analyzed.txt", inp=["data.txt"], out=["analyzed.txt"])
else:
    static("analyzed.txt")
step("cat analyzed.txt", inp=["analyzed.txt"])
