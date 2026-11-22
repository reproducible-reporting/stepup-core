#!/usr/bin/env python3
import os

from stepup.core.api import amend

# A step asks for a recheck at every start of the director while it runs.
amend(recheck=True)
with open("startup_i.txt", "w", encoding="utf-8", newline="\n") as fh:
    print(os.environ["STEPUP_STARTUP_I"], file=fh)
