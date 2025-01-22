#!/usr/bin/env python3
from stepup.core.api import call, static, step

static("work.py")
work = call("work.py", a=20, b=22, inp=True)
assert work.inp == ["work.py", "work_inp.json"]
assert work.out == []
step("cat work_out.json", inp="work_out.json")
