#!/usr/bin/env python3
from stepup.core.api import call, static

static("first.py", "second.py", "data.txt")
call("./first.py", "transform", inp=["data.txt"], out=["intermediate.txt"], planning=True)
