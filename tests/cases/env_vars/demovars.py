#!/usr/bin/env python
import json

from stepup.core.api import step

with open("variables.json") as fh:
    env_vars = json.load(fh)
info = step(
    "./printvars.py",
    inp=["printvars.py"],
    out=["current_variables.txt"],
    env=env_vars,
)

# Test info object, only useful for testing:
if info.env != env_vars:
    raise AssertionError("Wrong info.env")
