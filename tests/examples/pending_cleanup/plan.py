#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("echo data > ${out}", inp="missing.txt", out="hello.txt")
runsh("echo soon gone > ${out}", out="bye.txt")
