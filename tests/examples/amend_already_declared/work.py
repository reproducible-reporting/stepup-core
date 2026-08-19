#!/usr/bin/env python3
import os
from shutil import copy

from stepup.core.api import amend

# All four are already declared in plan.py, so this call changes nothing.
amend(inp=["inp.txt"], env=["VAR"], out=["out.txt"], vol=["vol.txt"])
copy("inp.txt", "out.txt")
with open("vol.txt", "w") as fh:
    fh.write(os.getenv("VAR", "unset") + "\n")
