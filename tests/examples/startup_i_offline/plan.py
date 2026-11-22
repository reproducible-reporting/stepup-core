#!/usr/bin/env python3
from stepup.core.api import run, static

static("pull.py", "push.py", "template.txt")
# The download and upload steps look at the remote at every start of the director.
run("./pull.py", env="STEPUP_STARTUP_I", out="data.txt")
run(
    "grep -v '^#' template.txt | cat - data.txt > report.txt",
    shell=True,
    inp=["template.txt", "data.txt"],
    out="report.txt",
)
run("./push.py", env="STEPUP_STARTUP_I", inp="report.txt")
