#!/usr/bin/env python
from stepup.core.api import glob, step

glob("static/**", _defer=True)
step("cat static/foo.txt", inp="static/foo.txt")
