#!/usr/bin/env python3
from stepup.core.api import glob, static

static("src/")
print("FILES:", sorted(str(p) for p in glob("src/*.txt")))
