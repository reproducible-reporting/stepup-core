#!/usr/bin/env python3
from stepup.core.api import glob, step

glob("README.*", _defer=True)
glob("*.md", _defer=True)
step("cat README.txt", inp="README.txt")
