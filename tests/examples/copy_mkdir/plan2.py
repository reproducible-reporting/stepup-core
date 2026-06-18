#!/usr/bin/env python3
from stepup.core.api import copy, run

run("echo hello > ${out}", shell=True, out="hello.txt")
copy("hello.txt", "other/")
