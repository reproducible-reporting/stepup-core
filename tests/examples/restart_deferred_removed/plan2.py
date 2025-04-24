#!/usr/bin/env python3
from stepup.core.api import copy, glob

glob("static/**", _defer=True)
copy("static/bar.txt", "./")
