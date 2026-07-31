#!/usr/bin/env python3
from stepup.core.api import glob, run

# The step declaring out.txt as an output comes first.
run("touch out.txt", shell=True, out="out.txt")

# out.txt already exists on disk (see main.sh), so the pattern's filesystem scan sees
# it. Eager check (a), in register_glob, rejects a pattern that matches a file another
# step already builds.
list(glob("*.txt"))
