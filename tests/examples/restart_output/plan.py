#!/usr/bin/env python3
from stepup.core.api import copy, static

static("original.txt")
copy("original.txt", "copy.txt")
