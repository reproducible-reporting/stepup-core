#!/usr/bin/env python
from stepup.core.api import static, step

static("source_both.txt")
step("cp source_both.txt copy_both1.txt", inp=["source_both.txt"], out=["copy_both1.txt"])
step(
    "cp source_01.txt copy_01.txt",
    inp=["source_01.txt"],
    out=["copy_01.txt"],
)
static("source_01.txt")
