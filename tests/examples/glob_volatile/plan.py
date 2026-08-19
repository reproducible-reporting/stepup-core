#!/usr/bin/env python3
from stepup.core.api import glob, run

# vol.txt already exists on disk (see main.sh), so the pattern's file system scan sees
# it. Eager check (a) treats a VOLATILE output the same as PLANNED/BUILT/OUTDATED.
run("touch vol.txt", shell=True, vol="vol.txt")
list(glob("*.txt"))
