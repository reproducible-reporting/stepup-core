#!/usr/bin/env python3
from stepup.core.api import run, static

static("./original.txt")
run("cp between.txt another.txt", inp=["between.txt"], out=["another.txt"])
run("cp original.txt between.txt", inp=["original.txt"], out=["./between.txt"])
