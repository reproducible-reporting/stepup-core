#!/usr/bin/env python3
from stepup.core.api import static, step

static("data/", "data/sub/", "data/sub/inp.txt")
step("cat ${inp} > ${out}", inp="data/sub/inp.txt", out="data/sub/out.txt")
