#!/usr/bin/env python3
from stepup.core.api import amend
from stepup.core.utils import filter_dependencies

paths = filter_dependencies(["bar.py", "venv/foo.py"])
amend(vol="paths.txt")
with open("paths.txt", "w") as fh:
    for path in paths:
        print(path, file=fh)
