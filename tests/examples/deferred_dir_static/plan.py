#!/usr/bin/env python3
from stepup.core.api import copy, glob, static

glob("given*/", _defer=True)
static("given1/inp.txt")
copy("given1/inp.txt", "out.txt")
