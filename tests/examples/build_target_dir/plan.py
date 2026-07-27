#!/usr/bin/env python3
from stepup.core.api import call, copy, static

static("a_input.txt", "b_input.txt", "c_input.txt", "other_input.txt", "gen.py")
copy("a_input.txt", "out/a.txt")
copy("b_input.txt", "out/b.txt")
copy("c_input.txt", "out/c.txt")
copy("other_input.txt", "other.txt")
call("./gen.py", "run", out=["out/result.txt"], vol=["invocations.txt"])
