#!/usr/bin/env python3
from stepup.core.api import copy, mkdir, step

mkdir("sub/")
step("echo hello > sub/inp.txt", out="sub/inp.txt")
copy("sub/inp.txt", "sub/out.txt")
