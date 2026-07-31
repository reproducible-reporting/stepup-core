#!/usr/bin/env python3
from stepup.core.api import run, shq, static

static("gen.py")
run(lambda out: f"./gen.py {shq(out)}", out=["first.txt", "second.txt"])
run(
    lambda inp, out: f"cat {shq(inp)} > {shq(out)}",
    shell=True,
    inp=["first.txt", "second.txt"],
    out="both.txt",
)
