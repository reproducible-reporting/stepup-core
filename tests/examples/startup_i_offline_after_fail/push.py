#!/usr/bin/env python3
"""Copy `report.txt` to the external data, unless that copy is up to date.

Offline, send nothing.
"""

import os

if os.environ.get("STEPUP_BUILD_OFFLINE") == "1":
    print("Offline, not sending report.txt.")
else:
    with open("report.txt", encoding="utf-8") as fh:
        local = fh.read()
    try:
        with open("external/report.txt", encoding="utf-8") as fh:
            external = fh.read()
    except FileNotFoundError:
        external = None
    if local != external:
        with open("external/report.txt", "w", encoding="utf-8", newline="\n") as fh:
            fh.write(local)
