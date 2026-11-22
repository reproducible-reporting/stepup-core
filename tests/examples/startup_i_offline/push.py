#!/usr/bin/env python3
"""Copy `report.txt` to the remote, unless the remote copy is up to date.

Offline, send nothing.
"""

from stepup.core.api import is_offline

if is_offline():
    print("Offline, not sending report.txt.")
else:
    with open("report.txt", encoding="utf-8") as fh:
        local = fh.read()
    try:
        with open("remote/report.txt", encoding="utf-8") as fh:
            remote = fh.read()
    except FileNotFoundError:
        remote = None
    if local != remote:
        with open("remote/report.txt", "w", encoding="utf-8", newline="\n") as fh:
            fh.write(local)
