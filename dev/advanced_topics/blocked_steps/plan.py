#!/usr/bin/env python3
from stepup.core.api import copy, runsh

runsh("echo hello > ${out}", out="a.txt")
copy("a.txt", "b.txt", resources="blocked")
copy("b.txt", "c.txt")
