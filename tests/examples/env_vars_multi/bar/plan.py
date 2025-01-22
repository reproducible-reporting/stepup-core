#!/usr/bin/env python3
from stepup.core.api import copy, getenv

inps = getenv("INPS", back=True, multi=True)
for inp in inps:
    out = inp.parent / "out.txt"
    copy(inp, out)
