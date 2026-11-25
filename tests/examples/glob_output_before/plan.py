#!/usr/bin/env python3
from stepup.core.api import glob, run

# The step declaring sub/out.txt as an output comes first.
run("touch sub/out.txt", shell=True, out="sub/out.txt")

# Eager check (a), in register_nglob, rejects a pattern that matches a declared output.
# The stale sub/out.txt on disk (see main.sh) plays no role in this check.
list(glob("sub/*.txt"))
