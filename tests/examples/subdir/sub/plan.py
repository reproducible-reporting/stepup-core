#!/usr/bin/env python3
# Here you can define steps working in the sub directory.
# The fact that the director runs in a different directory is not a problem.

import os

from path import Path

from stepup.core.api import run, shq

run(
    "tr '[:lower:]' '[:upper:]' < example.txt > upper.txt",
    shell=True,
    inp=["example.txt"],
    out=["upper.txt"],
)

stepup_root = Path(os.environ["STEPUP_ROOT"]).relpath()
inp_paths = [f"{stepup_root}/plan.py"]
run(f"cat {shq(inp_paths)}", inp=inp_paths)
