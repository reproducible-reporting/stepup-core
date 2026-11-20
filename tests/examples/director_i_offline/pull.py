#!/usr/bin/env python3
"""Copy the remote data to `data.txt`, unless `data.txt` is up to date.

Offline, keep `data.txt` when it exists, and fail otherwise.
"""

import os
import sys

try:
    with open("data.txt", encoding="utf-8") as fh:
        local = fh.read()
except FileNotFoundError:
    local = None
if os.environ.get("STEPUP_BUILD_OFFLINE") == "1":
    if local is None:
        sys.exit("Offline, and data.txt was never downloaded.")
    print("Offline, keeping data.txt.")
else:
    with open("remote/data.txt", encoding="utf-8") as fh:
        remote = fh.read()
    if local != remote:
        with open("data.txt", "w", encoding="utf-8", newline="\n") as fh:
            fh.write(remote)
