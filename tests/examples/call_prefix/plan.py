#!/usr/bin/env python3
from stepup.core.api import call, static

static("add.py")
call("add.py", a=10, b=12, prefix="add1")
call("add.py", a=8, b=90, prefix="add2")
