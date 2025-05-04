#!/usr/bin/env python3
import json

from stepup.core.api import runsh

with open("variables.json") as fh:
    env_vars = json.load(fh)
info = runsh(
    "./printvars.py",
    inp=["printvars.py"],
    out=["current_variables.txt"],
    env=env_vars,
)

# Test info object, only useful for testing:
if info.env != env_vars:
    raise AssertionError("Wrong info.env")
