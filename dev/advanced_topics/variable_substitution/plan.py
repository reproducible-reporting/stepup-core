#!/usr/bin/env python3
from stepup.core.api import run, static

static("step.py", "src_${MYVAR}.txt")
run("./step.py < ${inp} > ${out}", shell=True, inp="src_${MYVAR}.txt", out="dst_${MYVAR}.txt")
