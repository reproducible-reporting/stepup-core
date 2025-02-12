#!/usr/bin/env python3
from stepup.core.api import static, step

static("work1.py", "work2.py")
step("./work1.py", inp="work1.py")
step("./work2.py", inp="work2.py")
