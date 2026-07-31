#!/usr/bin/env python3
from stepup.core.api import glob, plan, static

static("sub/plan.py")

# The match is not yet justified by any declaration when this step runs: sub/plan.py
# (which declares the same files static) has not run yet. The end-of-phase check
# tolerates this because it only runs once every plan has had a chance to run --
# this is the case that forces the check to be late rather than eager.
list(glob("sub/*.txt"))
plan("./plan.py", workdir="sub")
