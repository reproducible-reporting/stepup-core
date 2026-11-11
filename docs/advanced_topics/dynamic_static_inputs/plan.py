#!/usr/bin/env python3
from stepup.core.api import amend, static

static("config.txt")
amend(inp="config.txt")
with open("config.txt", encoding="utf-8") as fh:
    print(fh.read().strip())
