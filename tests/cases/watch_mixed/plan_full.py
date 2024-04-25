#!/usr/bin/env python
from stepup.core.api import static, step

static("orig.txt")
step("cp orig.txt copy1.txt", inp=["orig.txt"], out=["copy1.txt"])
step("cp copy1.txt copy2.txt", inp=["copy1.txt"], out=["copy2.txt"])
