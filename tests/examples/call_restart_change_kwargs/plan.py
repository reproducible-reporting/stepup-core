#!/usr/bin/env python3
from stepup.core.api import call, loadns, static

static("work.py", "data.txt", "config.json")
config = loadns("config.json")
call("./work.py", "run", inp=["data.txt"], out=["result.txt"], factor=config.factor)
