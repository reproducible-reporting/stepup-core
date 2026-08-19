#!/usr/bin/env python3
from stepup.core.api import copy, getenv, static

tmp = getenv("mytmpdir", path=True)
static(tmp / "")
copy(tmp / "data.txt", "copy.txt")
