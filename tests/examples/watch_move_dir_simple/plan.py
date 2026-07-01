#!/usr/bin/env python3
from stepup.core.api import run, static

static("data/sub/inp.txt")
run(
    "cat data/sub/inp.txt > data/sub/out.txt",
    shell=True,
    inp="data/sub/inp.txt",
    out="data/sub/out.txt",
)
