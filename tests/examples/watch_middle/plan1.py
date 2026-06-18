#!/usr/bin/env python3
from stepup.core.api import run, static

run("cp copy.txt another.txt", inp=["copy.txt"], out=["another.txt"])
run("cp original.txt copy.txt", inp=["original.txt"], out=["./copy.txt"])
static("./original.txt")
