#!/usr/bin/env python3
import json

from stepup.core.api import glob

# Make data and subdirectories static when they are needed.
# In this example, they become static when files in them are declared static.
glob("data/**/", _defer=True)

# Make all inp and out files in data static and get them as separate lists.
# If all such files are needed in one list, use glob("data/**/*.*") instead.
paths_inp = glob("data/**/*.inp")
paths_out = glob("data/**/*.out")

# Write file lists to JSON files for testing.
with open("current_inp.json", "w") as fh:
    json.dump(sorted(paths_inp), fh, indent=2)
    fh.write("\n")
with open("current_out.json", "w") as fh:
    json.dump(sorted(paths_out), fh, indent=2)
    fh.write("\n")
