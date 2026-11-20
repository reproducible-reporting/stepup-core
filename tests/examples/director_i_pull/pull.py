#!/usr/bin/env python3
"""Copy the remote data to `data.txt`, unless `data.txt` is up to date."""

with open("remote/data.txt", encoding="utf-8") as fh:
    remote = fh.read()
try:
    with open("data.txt", encoding="utf-8") as fh:
        local = fh.read()
except FileNotFoundError:
    local = None
if local != remote:
    with open("data.txt", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(remote)
