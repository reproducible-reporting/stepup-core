#!/usr/bin/env python
from stepup.core.api import glob, copy

for m in glob("inp${*idx}.txt", idx="?"):
    copy(f"inp{m.idx}.txt", f"out{m.idx}.txt")
