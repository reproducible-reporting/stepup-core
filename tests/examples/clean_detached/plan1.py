#!/usr/bin/env python3
from stepup.core.api import run

run("echo hello1 > ${out}", shell=True, out="hello1.txt")
