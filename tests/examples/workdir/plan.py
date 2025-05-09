#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("sub/", "sub/inp.txt")
info = runsh("cp ${inp} ${out}", workdir="sub", inp="inp.txt", out="out.txt")
assert info.action == "runsh cp inp.txt out.txt"
assert info.workdir == "sub/"
assert info.inp == ["inp.txt"]
assert info.out == ["out.txt"]
