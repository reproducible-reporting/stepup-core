#!/usr/bin/env python3
from stepup.core.api import copy, static

static("input.txt")
copy("input.txt", "wanted.txt")
copy("input.txt", "other.txt")
