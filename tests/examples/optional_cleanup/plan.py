#!/usr/bin/env python3
from stepup.core.api import step

step("echo test1 > ${out}", out="test1.txt", optional=True)
step("echo test2 > ${out}", out="test2.txt", optional=True)
step("cat ${inp}", inp="test2.txt")
