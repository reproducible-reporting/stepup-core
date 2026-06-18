#!/usr/bin/env python3
from stepup.core.api import run, static

static("data/sub/inp.txt")
run("cat ${inp} > ${out}", shell=True, inp="data/sub/inp.txt", out="data/sub/out.txt")
