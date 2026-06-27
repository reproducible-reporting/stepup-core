#!/usr/bin/env python3
from stepup.core.api import copy, glob

for path_src in glob("src/*.txt"):
    copy(path_src, "dst/")
