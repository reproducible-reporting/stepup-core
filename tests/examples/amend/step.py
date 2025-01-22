#!/usr/bin/env python3
from shutil import copy

from stepup.core.api import amend

amend(inp=["inp2.txt"], out=["out2.txt"])
copy("inp1.txt", "out1.txt")
copy("inp2.txt", "out2.txt")
