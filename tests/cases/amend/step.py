#!/usr/bin/env python
from stepup.core.api import amend
from shutil import copy

if amend(inp=["inp2.txt"], out=["out2.txt"]):
    # Only perform the work when inp2.txt is available.
    copy("inp1.txt", "out1.txt")
    copy("inp2.txt", "out2.txt")
