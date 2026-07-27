#!/usr/bin/env python3
from stepup.core.api import copy, static

static("input.txt")
copy("input.txt", "report.txt")
copy("input.txt", "extra.txt")
copy("input.txt", "debug.txt", optional=True)
