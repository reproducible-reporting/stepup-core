#!/usr/bin/env python
from stepup.core.api import static, step

static("step.py", "src_${MYVAR}.txt")
step("./step.py < ${inp} > ${out}", inp="src_${MYVAR}.txt", out="dst_${MYVAR}.txt")
