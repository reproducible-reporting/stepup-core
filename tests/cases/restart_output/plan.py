#!/usr/bin/env python
from stepup.core.api import copy, static

static("original.txt")
copy("original.txt", "copy.txt")
