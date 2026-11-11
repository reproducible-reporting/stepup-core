#!/usr/bin/env python3
import sys

for path in sys.argv[1:]:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"content of {path}\n")
