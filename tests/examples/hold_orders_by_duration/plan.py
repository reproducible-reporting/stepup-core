#!/usr/bin/env python3
"""Hold three steps of increasing duration, declared in increasing (i.e. "wrong") order.

Without `hold()`, njob=2 leaves one job slot free once the plan step itself occupies the
other, so the first-declared step ("fast") would grab that free slot immediately, regardless
of cost. `hold()` defers dispatch until `release()`, so the batch becomes simultaneously
eligible only then, and the existing `_tail_time DESC` ordering picks the longest ("slow")
step first instead.
"""

import time
from pathlib import Path

from stepup.core.api import hold, static, step

static("work.py")

with hold():
    step("./work.py fast fast.txt", inp="work.py", out="fast.txt", duration=1.0)
    step("./work.py medium medium.txt", inp="work.py", out="medium.txt", duration=2.0)
    step("./work.py slow slow.txt", inp="work.py", out="slow.txt", duration=5.0)

    # A held step must stay ineligible for dispatch even though a job slot is free (njob=2:
    # one slot for this plan step, one free). This is a deterministic invariant -- `_safe`
    # requires `NOT creator._holding` -- not a timing race: any nonzero wait here is enough
    # to prove nothing was dispatched early.
    time.sleep(0.5)
    for name in ("fast.txt", "medium.txt", "slow.txt"):
        if Path(name).exists():
            raise RuntimeError(f"{name} was dispatched while still held")
