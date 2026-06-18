#!/usr/bin/env python3
from stepup.core.api import run

run("echo test1 > ${out}", shell=True, out="test1.txt", optional=True)
run("echo test2 > ${out}", shell=True, out="test2.txt", optional=True)
run("cat ${inp}", inp="test2.txt")
