#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("./inner.py", inp="inner.py", out="out.txt")
