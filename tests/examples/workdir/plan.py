#!/usr/bin/env python3
from stepup.core.api import run, static

static("sub/inp.txt")
info = run("cp ${inp} ${out}", workdir="sub", inp="inp.txt", out="out.txt")
assert info.action == "runexec cp inp.txt out.txt"
assert info.workdir == "sub/"
assert info.inp == ["inp.txt"]
assert info.out == ["out.txt"]
