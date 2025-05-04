#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("orig.txt")
runsh("cp orig.txt copy1.txt", inp=["orig.txt"], out=["copy1.txt"])
runsh("cp copy1.txt copy2.txt", inp=["copy1.txt"], out=["copy2.txt"])
