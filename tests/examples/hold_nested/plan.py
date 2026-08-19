#!/usr/bin/env python3
"""Nest two helper-based `hold()` batches inside one outer `hold()` block.

`declare_batch()` is a shared helper that itself uses `hold()`, called twice from within an
already-open outer `hold()` block. With `_holding` as a counter, steps declared in either
inner call stay held back until the *outer* block exits, not the inner one: each inner
`release()` only decrements the counter back to 1 (still nonzero), so nothing is dispatched
prematurely, and all three steps become eligible together only when the outer block closes.
"""

import time
from pathlib import Path

from stepup.core.api import hold, static, step

static("work.py")


def declare_batch(prefix, durations):
    """Declare one batch of `work.py` steps, each held until this call's block exits."""
    with hold():
        for name, duration in durations.items():
            step(
                f"./work.py {name} {prefix}_{name}.txt",
                inp="work.py",
                out=f"{prefix}_{name}.txt",
                duration=duration,
            )


with hold():
    declare_batch("a", {"fast": 1.0, "slow": 5.0})
    declare_batch("b", {"medium": 3.0})

    # None of the three steps -- from either nested batch -- may be dispatched while the
    # outer hold() is still open, even though a job slot is free (njob=2 below). This is a
    # deterministic invariant, not a timing race: any nonzero wait here is enough to prove
    # nothing escaped through an inner declare_batch() call's own release().
    time.sleep(0.5)
    for name in ("a_fast.txt", "a_slow.txt", "b_medium.txt"):
        if Path(name).exists():
            raise RuntimeError(f"{name} was dispatched while still held")
