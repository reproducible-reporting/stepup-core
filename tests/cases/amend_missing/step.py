#!/usr/bin/env python
from path import Path

from stepup.core.api import amend

# Simulate the accidental removal of the file
Path("missing.txt").remove_p()

# Then try to use it
amend(inp="missing.txt")
