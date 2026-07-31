#!/usr/bin/env python3
from stepup.core.api import glob, run

# The pattern is registered before any step declares a matching output.
list(glob("*.txt"))

# Declaring out.txt afterwards triggers eager check (b), in _raise_if_glob_match, with
# the same message text as glob_output_before's check (a).
run("touch out.txt", shell=True, out="out.txt")
