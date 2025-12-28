#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("step.py", "src_${MYVAR}.txt")
runsh("./step.py < ${inp} > ${out}", inp="src_${MYVAR}.txt", out="dst_${MYVAR}.txt")
