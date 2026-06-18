#!/usr/bin/env python3
from stepup.core.api import run

run("echo data > ${out}", shell=True, inp="missing.txt", out="hello.txt")
run("echo soon gone > ${out}", shell=True, out="bye.txt")
