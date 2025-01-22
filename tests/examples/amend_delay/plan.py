#!/usr/bin/env python3
from stepup.core.api import static, step

static("step.py")
step("./step.py > log.txt", inp="step.py", out="log.txt")
static("inp0.txt", "inp1.txt")
step("cp inp1.txt tmp1.txt", inp="inp1.txt", out="tmp1.txt")
step("echo Contents of inp2.txt > inp2.txt", out="inp2.txt")
