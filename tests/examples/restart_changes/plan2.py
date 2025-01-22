#!/usr/bin/env python3
from stepup.core.api import static, step

static("source_both.txt")
step("cp source_both.txt copy_both2.txt", inp=["source_both.txt"], out=["copy_both2.txt"])
static("source2.txt")
step(
    "cp source2.txt copy2.txt",
    inp=["source2.txt"],
    out=["copy2.txt"],
)
