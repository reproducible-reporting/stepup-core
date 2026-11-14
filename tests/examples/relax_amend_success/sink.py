#!/usr/bin/env python3
"""Mirror the compile_typst.py pattern: discover and read an input before amending it.

This step is dispatched concurrently with producer.sh (no declared dependency exists yet),
so it starts before producer.sh stops.
It waits for data.txt to appear, reads it, and only then calls amend().
On the first attempt, data.txt is unconditionally flagged "unfresh",
regardless of whether the read actually saw complete content,
because the check compares the order in which the steps started and stopped,
not the content of the file.
The step is therefore deferred.
On the next attempt, it starts after producer.sh has stopped,
so the dynamic input is accepted.
"""

import time
from pathlib import Path

from stepup.core.api import amend

data_path = Path("data.txt")
for _ in range(200):
    if data_path.is_file():
        break
    time.sleep(0.02)
else:
    raise RuntimeError("data.txt never appeared")

# The director learns that data.txt is BUILT slightly after the raw write completes on
# disk (it needs to notice the producer's subprocess exit and compute the file hash).
# Give it a comfortable margin so this step exercises the "BUILT but unfresh" freshness
# check rather than racing ahead into the pre-existing "not yet BUILT at all" path.
time.sleep(0.2)

content = data_path.read_text(encoding="utf-8")
amend(inp=["data.txt"])
print(f"sink read: {content.strip()}")
