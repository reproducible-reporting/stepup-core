#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("demovars.py")
runsh("./demovars.py > output.txt", inp=["demovars.py"], out=["output.txt"])
