#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("data/", "data/sub/", "data/sub/inp.txt")
runsh("cat ${inp} > ${out}", inp="data/sub/inp.txt", out="data/sub/out.txt")
