#!/usr/bin/env python3
from stepup.core.api import call, static

static("work.py", "data.txt")
call("./work.py", "run", inp=["data.txt"], out=["result.txt"], args_file="args.json")
