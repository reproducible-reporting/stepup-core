#!/usr/bin/env python3
from stepup.core.api import run, static

static("source_both.txt")
run("cp source_both.txt copy_both1.txt", inp=["source_both.txt"], out=["copy_both1.txt"])
run(
    "cp source1.txt copy1.txt",
    inp=["source1.txt"],
    out=["copy1.txt"],
)
static("source1.txt")
