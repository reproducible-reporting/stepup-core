#!/usr/bin/env python3
from stepup.core.api import glob, run

# The step declaring sub/out.txt as an output comes first.
run("touch sub/out.txt", shell=True, out="sub/out.txt")

# sub/out.txt already exists on disk (see main.sh), so the pattern's file system scan sees
# it. Eager check (a), in register_nglob, rejects a pattern that matches a file another
# step already builds.
list(glob("sub/*.txt"))
