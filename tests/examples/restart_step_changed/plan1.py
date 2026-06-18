#!/usr/bin/env python3
from stepup.core.api import copy, run

run("echo 1 > original.txt", shell=True, out="original.txt")
copy("original.txt", "copy.txt")
