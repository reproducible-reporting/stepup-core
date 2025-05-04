#!/usr/bin/env python3
from stepup.core.api import glob, runsh

glob("README.*", _defer=True)
glob("*.md", _defer=True)
runsh("cat README.txt", inp="README.txt")
