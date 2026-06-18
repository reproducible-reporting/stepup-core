#!/usr/bin/env python3
from stepup.core.api import call, run, static

static("work.py")
work = call("work.py", a=20, b=22, inp=True)
assert work.inp == ["work.py", "work_inp.json"]
assert work.out == []
run("cat work_out.json", inp="work_out.json")
