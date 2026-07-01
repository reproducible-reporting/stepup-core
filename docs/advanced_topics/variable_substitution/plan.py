#!/usr/bin/env python3
from stepup.core.api import run, shq, static

static("step.py", "src_${MYVAR}.txt")
run(
    f"./step.py < {shq('src_${MYVAR}.txt')} > {shq('dst_${MYVAR}.txt')}",
    shell=True,
    inp="src_${MYVAR}.txt",
    out="dst_${MYVAR}.txt",
)
