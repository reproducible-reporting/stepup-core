#!/usr/bin/env python
import json

from stepup.core.api import step

with open("variables.json") as fh:
    env_vars = json.load(fh)
step(
    "./printvars.py",
    inp=["printvars.py"],
    out=["current_variables.txt"],
    env=env_vars,
)
