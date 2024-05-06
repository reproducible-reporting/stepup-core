#!/usr/bin/env python
from stepup.core.api import copy, glob

glob("u*.txt", _defer=True)
copy("used.txt", "copy.txt")
