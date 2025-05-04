#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("line.txt")
runsh("cat line.txt >> log.txt; exit 1", inp="line.txt", out="log.txt")
