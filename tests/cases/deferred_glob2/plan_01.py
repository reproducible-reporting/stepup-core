#!/usr/bin/env python
from stepup.core.api import glob, copy

glob("static/**", _defer=True)
copy("static/sub/foo.txt", "copy.txt")
