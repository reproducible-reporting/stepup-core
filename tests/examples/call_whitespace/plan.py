#!/usr/bin/env python3
from stepup.core.api import call, static

static("my work.py", "data.txt")
call("./my work.py", "run", inp=["data.txt"], out=["result.txt"])
