#!/usr/bin/env python3
from stepup.core.api import run, static

static("cite.py", "key.txt", "intro.txt")
run("./cite.py", inp="key.txt", out="citation.txt")
run(
    "cat intro.txt citation.txt > report.txt",
    shell=True,
    inp=["intro.txt", "citation.txt"],
    out="report.txt",
)
