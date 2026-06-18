#!/usr/bin/env python3
from stepup.core.api import run, static

static("line.txt")
run("cat line.txt >> log.txt; exit 1", shell=True, inp="line.txt", out="log.txt")
