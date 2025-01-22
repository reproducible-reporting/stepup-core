#!/usr/bin/env python3
from stepup.core.api import copy, glob

for m in glob("inp${*idx}.txt", idx="?"):
    copy(f"inp{m.idx}.txt", f"out{m.idx}.txt")
