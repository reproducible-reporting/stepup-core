#!/usr/bin/env python3
from stepup.core.api import copy, glob, static

# The tree owns every file under src/, so two overlapping globs can both match
# src/foo.txt without either one trying to declare it again.
static("src/")
for inp_path in glob("src/*.txt"):
    copy(inp_path, "out_" + inp_path.name)
for inp_path in glob("src/f*.txt"):
    copy(inp_path, "alt_" + inp_path.name)
