#!/usr/bin/env python3
import contextlib
import subprocess
import sys
from shutil import copy

from stepup.core.api import amend

with contextlib.chdir("sub"):
    amend(inp="data.txt", out=["copy1.txt", "${ROOT}/sub/copy3.txt"])
    copy("data.txt", "copy1.txt")
    copy("data.txt", "copy3.txt")

subprocess.run([sys.executable, "child.py"], cwd="sub", check=True)
