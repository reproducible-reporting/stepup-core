#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("echo test1 > ${out}", out="test1.txt", optional=True)
runsh("echo test2 > ${out}", out="test2.txt", optional=True)
runsh("cat ${inp}", inp="test2.txt")
