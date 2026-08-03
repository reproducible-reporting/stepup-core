#!/usr/bin/env python3
from stepup.core.api import amend

# A file inside a static tree is lazily declared static the first time it is
# used as a (dynamic) input, without static() ever naming it directly.
amend(inp="data/a/x.txt")
with open("data/a/x.txt") as fh:
    text = fh.read().strip()
with open("out.txt", "w") as fh:
    print(text, file=fh)
