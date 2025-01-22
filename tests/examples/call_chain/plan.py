#!/usr/bin/env python3
from stepup.core.api import call, static

static("add.py", "square.py")
add = call("add.py", a=20, b=22, inp=True)
assert add.inp == ["add.py", "add_inp.json"]
assert add.out == []
square = call("square.py", inp="add_out.json")
assert square.inp == ["add_out.json", "square.py"]
