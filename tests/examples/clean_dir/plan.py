#!/usr/bin/env python3
from stepup.core.api import copy, run

run("echo hello > sub/inp.txt", shell=True, out="sub/inp.txt")
copy("sub/inp.txt", "sub/tmp.txt")
copy("sub/tmp.txt", "sub/out.txt")
