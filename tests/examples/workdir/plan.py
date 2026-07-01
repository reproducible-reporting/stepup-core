#!/usr/bin/env python3
from stepup.core.api import run, static

static("sub/inp.txt")
info = run("cp inp.txt out.txt", workdir="sub", inp="inp.txt", out="out.txt")
assert info.command == "cp inp.txt out.txt"
assert info.workdir == "sub"
assert info.inp == ["inp.txt"]
assert info.out == ["out.txt"]
