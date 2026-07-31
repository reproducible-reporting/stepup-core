#!/usr/bin/env python3
from stepup.core.api import copy, glob, static

ng = glob("inp${*idx}.txt", idx="?")
static(ng)
for m in ng:
    copy(f"inp{m.idx}.txt", f"out{m.idx}.txt")
