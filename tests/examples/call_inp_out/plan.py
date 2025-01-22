#!/usr/bin/env python3
from stepup.core.api import call, static

static("special.py")
special = call("special.py", order=42, inp="foo.pickle", out="bar.json")
assert special.inp == ["foo.pickle", "special.py"]
assert special.out == ["bar.json"]
