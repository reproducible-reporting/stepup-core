#!/usr/bin/env python3
"""Copy the external data to `data.txt`, unless `data.txt` is up to date."""

with open("external/data.txt", encoding="utf-8") as fh:
    external = fh.read()
try:
    with open("data.txt", encoding="utf-8") as fh:
        local = fh.read()
except FileNotFoundError:
    local = None
if local != external:
    with open("data.txt", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(external)
