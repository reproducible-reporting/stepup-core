#!/usr/bin/env python3
from stepup.core.api import call, static

static("generate.py")
call("generate.py", out="data1.json", size=10)
call("generate.py", out="data2.json", size=20)
