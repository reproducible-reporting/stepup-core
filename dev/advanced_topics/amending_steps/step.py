#!/usr/bin/env python3
from stepup.core.api import amend

# Parse the sources.txt file
with open("sources.txt") as fh:
    paths_inp = fh.read().split()

# Request the additional input.
amend(inp=paths_inp)

# Write all files from source.txt to the standard output.
# This part is reachable only if the amended input is present.
for path_inp in paths_inp:
    print(f"Contents of {path_inp}:")
    with open(path_inp) as fh:
        print(fh.read())
