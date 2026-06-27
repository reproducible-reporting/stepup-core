#!/usr/bin/env python3
from stepup.core.api import copy, static

static("example.txt")
copy("example.txt", "${ROOT}/../public/${HERE}/")
