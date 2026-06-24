#!/usr/bin/env python3
import shlex
import sys

from stepup.core.extapi import run_subprocess

# Run a real subprocess and record it for archival. The subprocess writes the step output
# and receives an environment overlay (GREETING) on top of the inherited environment.
# Only the overlay is recorded, not the full resolved environment.
# The command is a single shell-quoted string.
parts = [
    shlex.quote(sys.executable),
    "-c",
    """'import os; open("out.txt", "w").write(os.environ["GREETING"])'""",
]
command = " ".join(parts)
run_subprocess(command, env={"GREETING": "hello"})
