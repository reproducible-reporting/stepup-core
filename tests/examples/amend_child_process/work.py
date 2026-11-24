#!/usr/bin/env python3
import json
import subprocess
import sys

from stepup.core.api import amend, get_amended_info, get_info
from stepup.core.cattrs import json_converter
from stepup.core.stepinfo import StepInfo, dump_step_info

# A dependency amended by the step itself.
amend(out="extra.txt")
with open("extra.txt", "w", encoding="utf-8", newline="\n") as fh:
    fh.write("extra\n")

# The child process amends its own dependencies and reports them on its standard output.
cp = subprocess.run(
    [sys.executable, "child.py"], cwd="child", check=True, capture_output=True, text=True
)
child_info = json_converter.structure(json.loads(cp.stdout), StepInfo)
assert child_info.workdir == "child"

# Combine the declared dependencies with those amended by both processes.
info = get_info().merge(get_amended_info(), child_info)
info.out = [path for path in info.out if path != "current_all_info.json"]
dump_step_info("current_all_info.json", info)
