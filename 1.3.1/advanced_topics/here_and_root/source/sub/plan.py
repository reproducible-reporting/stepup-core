#!/usr/bin/env python
from stepup.core.api import copy, mkdir, static

static("example.txt")
dst = "${ROOT}/../public/${HERE}/"
mkdir(dst)
copy("example.txt", dst)
