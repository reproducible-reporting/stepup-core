#!/usr/bin/env python3
from stepup.core.api import run, static

static("inp.txt", "work.py")
run("./work.py", inp=["inp.txt"], env=["VAR"], out=["out.txt"], vol=["vol.txt"])
