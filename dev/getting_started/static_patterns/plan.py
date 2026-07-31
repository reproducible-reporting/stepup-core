#!/usr/bin/env python3
from stepup.core.api import copy, static

for path_src in static("src/*.txt"):
    copy(path_src, "dst/")
