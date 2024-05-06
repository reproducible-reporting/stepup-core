#!/usr/bin/env python
from stepup.core.api import copy, glob

glob("static/**", _defer=True)
copy("static/sub/foo.txt", "copy.txt")
