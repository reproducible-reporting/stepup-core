#!/usr/bin/env python3
from stepup.core.api import getinfo

# This is a somewhat poor example, because plan.py script should normally not do
# much more than just calling runsh() function.
# This example is just meant to show that it is possible
# to use input files and write output files.
# It also demonstrates how to use the getinfo() function.

info = getinfo()

# Just checking for the sake of the test:
print(info)
assert info.action == "runpy ./plan.py"
assert info.workdir == "sub/"
assert info.inp == ["inp.txt", "plan.py"]
assert info.env == []
assert info.out == ["out.txt"]
assert info.vol == []

with open(info.filter_inp("*.txt").single()) as f:
    text = f.read()
with open(info.out[0], "w") as f:
    f.write(text.upper())
