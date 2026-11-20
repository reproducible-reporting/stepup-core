#!/usr/bin/env python3
"""Copy `local.txt` to the remote.

The argument only identifies the work for StepUp and is not used here.
"""

with open("local.txt", "rb") as fh:
    data = fh.read()
with open("remote/data.txt", "wb") as fh:
    fh.write(data)
