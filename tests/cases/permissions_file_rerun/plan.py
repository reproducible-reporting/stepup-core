#!/usr/bin/env python
from stepup.core.api import copy, static

static("input.txt")
copy("input.txt", "output.txt")
