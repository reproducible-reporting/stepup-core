#!/usr/bin/env python3
from stepup.core.api import copy, glob

glob("u*.txt", _defer=True)
copy("used.txt", "copy.txt")
