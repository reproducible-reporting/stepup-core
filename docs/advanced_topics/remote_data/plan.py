#!/usr/bin/env python3
from stepup.core.api import run, static

static("download.py", "report.py")
run("./download.py", out="data.txt", recheck=True)
run("./report.py", inp="data.txt", out="report.txt")
