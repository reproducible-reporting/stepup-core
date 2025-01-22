#!/usr/bin/env python3
from stepup.core.api import glob

m = glob("data/**")
for path in m:
    print(path)
