#!/usr/bin/env python3
import json

from stepup.core.api import glob, static

# Make data/ a static tree, then get the inp and out files as separate lists.
# If all such files are needed in one list, use glob("data/**/*.*") instead.
static("data/")
paths_inp = glob("data/**/*.inp")
paths_out = glob("data/**/*.out")

# Write file lists to JSON files for testing.
with open("current_inp.json", "w") as fh:
    json.dump(sorted(paths_inp), fh, indent=2)
    fh.write("\n")
with open("current_out.json", "w") as fh:
    json.dump(sorted(paths_out), fh, indent=2)
    fh.write("\n")
