#!/usr/bin/env python
from stepup.core.api import glob

m = glob("data/**")
for path in m:
    print(path)
