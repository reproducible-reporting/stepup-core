#!/usr/bin/env python3
from stepup.core.api import getenv

with open("director_i.txt", "w", encoding="utf-8", newline="\n") as fh:
    print(getenv("STEPUP_DIRECTOR_I"), file=fh)
