#!/usr/bin/env python3
from stepup.core.api import script, static, step

static("inp1.txt", "inp2.txt", "work.py", "prep.py")
step("./work.py", inp=["inp2.txt", "work.py"], out="out2.txt")
script("prep.py")
