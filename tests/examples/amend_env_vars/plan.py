#!/usr/bin/env python3
from stepup.core.api import run, static

static("demovars.py")
run("./demovars.py > output.txt", shell=True, inp=["demovars.py"], out=["output.txt"])
