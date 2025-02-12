#!/usr/bin/env python3
from stepup.core.api import static, step

static("work.py")
step("echo ping > ping.txt", out="ping.txt")
step("echo pong > pong.txt", out="pong.txt")
step("./work.py", inp="work.py", out="work.txt")
