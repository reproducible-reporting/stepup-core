#!/usr/bin/env python
from stepup.core.api import amend

if amend(inp=["inp0.txt", "tmp1.txt", "inp2.txt"]):
    with open("inp0.txt") as fh:
        print(fh.read().strip())
    with open("tmp1.txt") as fh:
        print(fh.read().strip())
    with open("inp2.txt") as fh:
        print(fh.read().strip())
