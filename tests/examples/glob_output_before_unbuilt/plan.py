#!/usr/bin/env python3
from stepup.core.api import glob, run

# The step declaring sub/out.txt as an output comes first.
run("touch sub/out.txt", shell=True, out="sub/out.txt")

# sub/out.txt does not exist yet when the pattern is registered,
# but eager check (a), in register_nglob, rejects it anyway as a declared output.
list(glob("sub/*.txt"))
