#!/usr/bin/env python3
from stepup.core.extapi import run_subprocess

# Run a real subprocess and record it for archival. The subprocess writes the step output
# and receives an environment overlay (GREETING) on top of the inherited environment.
# Only the overlay is recorded, not the full resolved environment.
code = 'import os, sys; print(os.environ["GREETING"]); print(sys.stdin.read(), file=sys.stderr)'
command = f"GREETING=hello python -c '{code}'"
run_subprocess(command, stdin="world", stdout=None, stderr=None)
