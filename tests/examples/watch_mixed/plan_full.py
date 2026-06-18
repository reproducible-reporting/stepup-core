#!/usr/bin/env python3
from stepup.core.api import run, static

static("orig.txt")
run("cp orig.txt copy1.txt", inp=["orig.txt"], out=["copy1.txt"])
run("cp copy1.txt copy2.txt", inp=["copy1.txt"], out=["copy2.txt"])
