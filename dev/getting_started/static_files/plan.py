#!/usr/bin/env python3
from stepup.core.api import run, static

static("limerick.txt")
run("nl limerick.txt > numbered.txt", shell=True, inp="limerick.txt", out="numbered.txt")
