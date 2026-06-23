#!/usr/bin/env python3
import subprocess
import sys

# Python-level output goes to stdout only, so the "Standard error" page below can
# only appear if the subprocess's stderr is captured (the behaviour under test).
print("python-stdout-line")
# Flush so the non-fork path orders Python output before the subprocess output,
# matching the forkserver path's concatenation order.
sys.stdout.flush()
subprocess.run(
    ["sh", "-c", "echo subprocess-stdout-line; echo subprocess-stderr-line >&2"],
    check=True,
)
