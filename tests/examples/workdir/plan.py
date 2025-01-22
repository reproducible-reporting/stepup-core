#!/usr/bin/env python3
from stepup.core.api import static, step

static("sub/", "sub/inp.txt")
info = step("cp ${inp} ${out}", workdir="sub", inp="inp.txt", out="out.txt")
assert info.command == "cp inp.txt out.txt"
assert info.workdir == "sub/"
assert info.inp == ["inp.txt"]
assert info.out == ["out.txt"]
