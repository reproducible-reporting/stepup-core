#!/usr/bin/env python3
from stepup.core.api import amend, static

static("data.txt")
amend(inp="data.txt", out="copy.txt")
with open("data.txt") as f1, open("copy.txt", "w") as f2:
    f2.write(f1.read())
