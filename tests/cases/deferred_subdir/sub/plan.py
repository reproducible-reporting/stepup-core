#!/usr/bin/env python
from stepup.core.api import glob, copy

glob("u*.txt", _defer=True)
copy("used.txt", "copy.txt")
