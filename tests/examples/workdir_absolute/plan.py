#!/usr/bin/env python3
import os

from stepup.core.api import run, static

root = os.environ["STEPUP_ROOT"]
static("sub/a.txt")
run("cp a.txt b.txt", workdir=f"{root}/sub", inp="a.txt", out="b.txt")
run("cp b.txt c.txt", workdir="sub", inp="b.txt", out="c.txt")
run("cp c.txt d.txt", workdir=f"{root}/sub", inp="c.txt", out="d.txt")
