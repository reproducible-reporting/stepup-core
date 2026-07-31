#!/usr/bin/env python3
from stepup.core.api import glob, static

static("data/")
m = glob("data/**/*.*")
for path in m:
    print(path)
