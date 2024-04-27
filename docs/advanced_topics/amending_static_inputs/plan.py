#!/usr/bin/env python
from stepup.core.api import static, amend

static("config.txt")
amend(inp="config.txt")
with open("config.txt") as fh:
    print(fh.read().strip())
