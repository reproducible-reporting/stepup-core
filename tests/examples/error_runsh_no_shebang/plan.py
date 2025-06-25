#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("simple.sh")
runsh("./simple.sh", inp="simple.sh")
