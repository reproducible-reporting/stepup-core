#!/usr/bin/env python
from stepup.core.api import glob, step

glob("README.*", _defer=True)
glob("*.md", _defer=True)
step("cat README.md", inp="README.md")
