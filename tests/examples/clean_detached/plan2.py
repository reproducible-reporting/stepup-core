#!/usr/bin/env python3
from stepup.core.api import run

run("echo hello2 > ${out}", shell=True, out="hello2.txt")
