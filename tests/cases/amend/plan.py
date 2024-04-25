#!/usr/bin/env python
from stepup.core.api import static, step

static("inp1.txt")
step("./step.py", inp=["inp1.txt"], out=["out1.txt"])
step("echo word2 > inp2.txt", out=["inp2.txt"])
