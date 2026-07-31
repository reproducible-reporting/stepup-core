#!/usr/bin/env python3
from stepup.core.api import copy, glob, run, shq, static

# Declare the sources once. The queries below only look at them.
static("src/")

# A query over all source files.
for path_src in glob("src/*.txt"):
    copy(path_src, "dst/")

# An overlapping query, collecting only the notes.
paths_notes = glob("src/*_notes.txt").files()
run(f"cat {shq(paths_notes)} > notes.txt", shell=True, inp=paths_notes, out="notes.txt")
