#!/usr/bin/env python
from stepup.core.api import glob, copy, mkdir, static

static("src/")
mkdir("dst/")
for path_src in glob("src/*.txt"):
    copy(path_src, "dst/")
