#!/usr/bin/env python3
from stepup.core.api import static, step

static("work.py")
step("./work.py", inp="work.py", out="work.txt")
step("echo ping > ping.txt", out="ping.txt")
