#!/usr/bin/env python3
from stepup.core.api import copy, static

static("inp.txt")
copy("inp.txt", "out.txt")
copy("out.txt", "final.txt")
