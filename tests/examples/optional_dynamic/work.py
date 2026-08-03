#!/usr/bin/env python3
from stepup.core.api import amend

amend(inp="optional.txt")
with open("work.out", "w") as f:
    f.write("bye world\n")
