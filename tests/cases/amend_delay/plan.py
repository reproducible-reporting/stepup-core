#!/usr/bin/env python
from stepup.core.api import static, step

step("sleep 0.1; cp inp1.txt tmp1.txt", inp="inp1.txt", out="tmp1.txt")
step("./step.py > log.txt", inp="step.py", out="log.txt")
step("sleep 0.1; echo Contents of inp2.txt > inp2.txt", out="inp2.txt")
static("inp0.txt", "inp1.txt", "step.py")
