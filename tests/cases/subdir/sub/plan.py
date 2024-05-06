#!/usr/bin/env python
# Here you can define steps working in the sub directory.
# The fact that the director runs in a different directory is not a problem.

import os

from path import Path

from stepup.core.api import step

step(
    "tr '[:lower:]' '[:upper:]' < example.txt > upper.txt",
    inp=["example.txt"],
    out=["upper.txt"],
)

stepup_root = Path(os.environ["STEPUP_ROOT"]).relpath()
step("cat ${inp}", inp=[f"{stepup_root}/plan.py"])
