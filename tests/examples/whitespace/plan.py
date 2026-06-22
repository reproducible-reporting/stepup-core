#!/usr/bin/env python3
from stepup.core.api import copy, script, static

static("README NOW.txt", "script is cool.py")
copy("README NOW.txt", "white space leaves a lot of room for mistakes.txt")
script("script is cool.py")
