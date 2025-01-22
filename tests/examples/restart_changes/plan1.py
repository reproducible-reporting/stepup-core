#!/usr/bin/env python3
from stepup.core.api import static, step

static("source_both.txt")
step("cp source_both.txt copy_both1.txt", inp=["source_both.txt"], out=["copy_both1.txt"])
step(
    "cp source1.txt copy1.txt",
    inp=["source1.txt"],
    out=["copy1.txt"],
)
static("source1.txt")
