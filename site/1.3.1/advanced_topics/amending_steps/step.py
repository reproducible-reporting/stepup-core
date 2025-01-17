#!/usr/bin/env python
from stepup.core.api import amend

# Parse the sources.txt file
with open("sources.txt") as fh:
    paths_inp = fh.read().split()

# Write all files from source.txt to the standard output.
keep_going = amend(inp=paths_inp)
if keep_going:
    # This branch is only executed if the amended input is present.
    for path_inp in paths_inp:
        print(f"Contents of {path_inp}:")
        with open(path_inp) as fh:
            print(fh.read())
