#!/usr/bin/env python
from stepup.core.api import step, copy

step("echo hello > ${out}", out="a.txt")
copy("a.txt", "b.txt", block=True)
copy("b.txt", "c.txt")
