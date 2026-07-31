#!/usr/bin/env python3
from stepup.core.api import run, shq, static


def upper(src, dst):
    run(f"tr '[:lower:]' '[:upper:]' < {shq(src)} > {shq(dst)}", shell=True, inp=src, out=dst)


for path in static("lower*.txt"):
    upper(path, "upper" + path[5:])
