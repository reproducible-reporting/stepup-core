#!/usr/bin/env python3
from stepup.core.api import copy, runsh

runsh("echo hello > ${out}", out="a.txt")
copy("a.txt", "b.txt", block=True)
copy("b.txt", "c.txt")
