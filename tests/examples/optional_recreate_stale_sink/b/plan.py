#!/usr/bin/env python3
from stepup.core.api import run

# Non-optional consumer of `a`'s output, in a sub-plan that never reruns in this
# example (its own plan.py never changes), so it can never independently
# re-flag anything -- see README.txt.
run("cp ../a/hop2.txt done.txt", shell=True, inp="../a/hop2.txt", out="done.txt")
