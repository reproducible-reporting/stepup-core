#!/usr/bin/env python3
from stepup.core.api import copy, run

run("echo hello > a.txt", shell=True, out="a.txt")
copy("a.txt", "b.txt", resources="blocked")
copy("b.txt", "c.txt")
