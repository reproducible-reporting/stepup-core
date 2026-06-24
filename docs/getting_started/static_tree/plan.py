#!/usr/bin/env python3
from stepup.core.api import copy, static

static("data/")
copy("data/somefile.txt", "out/")
