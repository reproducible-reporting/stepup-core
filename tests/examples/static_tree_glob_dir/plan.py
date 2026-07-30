#!/usr/bin/env python3
from stepup.core.api import glob, static

# A directory match is only accepted when it lies inside a static tree.
static("src/")
for sub_path in glob("src/*/"):
    print(f"Found subdirectory: {sub_path}")
