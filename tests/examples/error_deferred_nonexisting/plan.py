#!/usr/bin/env python3
from stepup.core.api import glob, runsh

glob("static/**", _defer=True)
runsh("cat static/foo/bar/README.md", inp="static/foo/bar/README.md")
