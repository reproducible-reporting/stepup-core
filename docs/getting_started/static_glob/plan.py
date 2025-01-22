#!/usr/bin/env python3
from stepup.core.api import copy, glob, mkdir, static

static("src/")
mkdir("dst/")
for path_src in glob("src/*.txt"):
    copy(path_src, "dst/")
