#!/usr/bin/env python3
from stepup.core.api import copy, mkdir, static

static("example.txt")
dst = "${ROOT}/../public/${HERE}/"
mkdir(dst)
copy("example.txt", dst)
