#!/usr/bin/env python3
from stepup.core.api import run, static

static("input.txt")
run("cp input.txt output.txt", shell=True, inp="input.txt", out="output.txt")
