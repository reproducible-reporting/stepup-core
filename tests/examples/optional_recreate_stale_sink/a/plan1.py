#!/usr/bin/env python3
from stepup.core.api import run, static

static("hop1.txt")
# Optional producer: only runs if (indirectly) needed by a non-optional step.
run("cp hop1.txt hop2.txt", shell=True, inp="hop1.txt", out="hop2.txt", optional=True)
