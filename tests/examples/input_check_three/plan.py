#!/usr/bin/env python3
from stepup.core.api import static, step

static("work.py")
step("echo hi > f1.txt", out="f1.txt")
step("./work.py", inp="work.py", out="f2.txt")
# The following is never executed because work will tamper with f1.txt
step("cat f1.txt f2.txt > f3.txt", inp=["f1.txt", "f2.txt"], out="f3.txt")
