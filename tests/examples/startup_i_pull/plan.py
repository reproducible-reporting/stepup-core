#!/usr/bin/env python3
from stepup.core.api import run, static

static("pull.py")
# The download step looks at the remote at every start of the director.
run("./pull.py", out="data.txt", recheck=True)
run("tr a-z A-Z < data.txt > report.txt", shell=True, inp="data.txt", out="report.txt")
