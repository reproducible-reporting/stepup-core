#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("source_both.txt")
runsh("cp source_both.txt copy_both2.txt", inp=["source_both.txt"], out=["copy_both2.txt"])
static("source2.txt")
runsh(
    "cp source2.txt copy2.txt",
    inp=["source2.txt"],
    out=["copy2.txt"],
)
