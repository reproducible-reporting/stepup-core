#!/usr/bin/env python3
from stepup.core.api import glob, run, shq


def upper(src, dst):
    run(f"tr '[:lower:]' '[:upper:]' < {shq(src)} > {shq(dst)}", shell=True, inp=src, out=dst)


for path in glob("lower*.txt"):
    upper(path, "upper" + path[5:])
