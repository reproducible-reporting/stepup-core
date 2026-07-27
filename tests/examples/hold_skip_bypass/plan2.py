#!/usr/bin/env python3
"""Redeclare "cached" (unchanged, must SKIP) next to a new, slow sibling, both held.

While still holding, this checks that "cached" has already been resolved via
`.stepup/success.log` -- proving hash-checkable jobs bypass an active `hold()` -- and that
the slow, hash-less sibling has not started, proving it stays gated by the hold like an
ordinary `RunJob`.
"""

import time
from pathlib import Path

from stepup.core.api import hold, static, step

static("work.py")

with hold():
    step("./work.py cached cached.txt", inp="work.py", out="cached.txt")
    step("./work.py --sleep=1.0 rerun rerun.txt", inp="work.py", out="rerun.txt")

    # Give the scheduler enough time to dispatch and resolve the hash-checkable "cached"
    # sibling. The slow "rerun" sibling sleeps for 1s once dispatched, well after this
    # check, so it cannot have produced its output yet even if (incorrectly) dispatched
    # early -- but the real point is that it must not have been *dispatched* at all, which
    # the absence of its START line below confirms.
    time.sleep(0.5)
    lines = Path(".stepup/success.log").read_text().splitlines()
    if not any("SKIP" in line and "cached" in line for line in lines):
        raise RuntimeError(
            "The unchanged 'cached' step was not skipped while the hold was still active."
        )
    if any("START" in line and "rerun" in line for line in lines):
        raise RuntimeError("The 'rerun' step was dispatched while still held.")
