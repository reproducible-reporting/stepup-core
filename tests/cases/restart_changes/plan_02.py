#!/usr/bin/env python
from stepup.core.api import static, step

static("source_both.txt")
step("cp source_both.txt copy_both2.txt", inp=["source_both.txt"], out=["copy_both2.txt"])
static("source_02.txt")
step(
    "cp source_02.txt copy_02.txt",
    inp=["source_02.txt"],
    out=["copy_02.txt"],
)
