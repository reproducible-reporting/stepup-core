#!/usr/bin/env python
from stepup.core.api import glob, step


def upper(src, dst):
    step("tr '[:lower:]' '[:upper:]' < ${inp} > ${out}", inp=src, out=dst)


for path in glob("lower*.txt"):
    upper(path, "upper" + path[5:])
