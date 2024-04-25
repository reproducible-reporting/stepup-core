#!/usr/bin/env python
from stepup.core.api import glob, step

glob("static/**", _defer=True)
step("cat static/foo/bar/README.md", inp="static/foo/bar/README.md")
