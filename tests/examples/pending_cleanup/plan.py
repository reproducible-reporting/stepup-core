#!/usr/bin/env python3
from stepup.core.api import step

step("echo data > ${out}", inp="missing.txt", out="hello.txt")
step("echo soon gone > ${out}", out="bye.txt")
