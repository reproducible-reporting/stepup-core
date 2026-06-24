#!/usr/bin/env python3
from stepup.core.api import call, static

static("sub/work.py", "sub/data.txt")
# workdir="sub" shifts the step's path context: inp/out are relative to sub/.
# The step's CWD when a Python script runs is the plan directory (not the workdir),
# so the function receives inp/out as workdir-relative paths.
call("./work.py", "run", inp=["data.txt"], out=["result.txt"], workdir="sub")
