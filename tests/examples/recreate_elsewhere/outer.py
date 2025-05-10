#!/usr/bin/env python3
from stepup.core.api import runpy

runpy("./inner.py", inp="inner.py", out="out.txt")
