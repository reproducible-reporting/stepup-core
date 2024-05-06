#!/usr/bin/env python
from stepup.core.api import copy, step

step("echo hello > ${out}", out="a.txt")
copy("a.txt", "b.txt", block=True)
copy("b.txt", "c.txt")
