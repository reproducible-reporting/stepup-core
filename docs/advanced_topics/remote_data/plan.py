#!/usr/bin/env python3
from stepup.core.api import run, static

static("download.py", "report.py")
run("./download.py", env="STEPUP_DIRECTOR_I", out="data.txt")
run("./report.py", inp="data.txt", out="report.txt")
