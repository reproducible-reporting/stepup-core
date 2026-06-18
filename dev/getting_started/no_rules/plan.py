#!/usr/bin/env python3
from stepup.core.api import glob, run


def upper(src, dst):
    run("tr '[:lower:]' '[:upper:]' < ${inp} > ${out}", shell=True, inp=src, out=dst)


for path in glob("lower*.txt"):
    upper(path, "upper" + path[5:])
