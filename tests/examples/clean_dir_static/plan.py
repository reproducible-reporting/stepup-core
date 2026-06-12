#!/usr/bin/env python3
from stepup.core.api import copy, static

static("sub/inp.txt")
copy("sub/inp.txt", "sub/tmp.txt")
copy("sub/tmp.txt", "sub/out.txt")
