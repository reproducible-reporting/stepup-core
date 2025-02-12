#!/usr/bin/env python3
from stepup.core.api import copy, static, step

static("work.py")
step("echo hi > f1.txt", out="f1.txt")
step("./work.py", inp=["work.py", "f1.txt"], out="f2.txt")
copy("f2.txt", "f3.txt")
