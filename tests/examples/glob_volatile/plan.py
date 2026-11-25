#!/usr/bin/env python3
from stepup.core.api import glob, run

# Eager check (a) treats a VOLATILE output the same as PLANNED/BUILT/OUTDATED.
# The stale vol.txt on disk (see main.sh) plays no role in this check.
run("touch vol.txt", shell=True, vol="vol.txt")
list(glob("*.txt"))
