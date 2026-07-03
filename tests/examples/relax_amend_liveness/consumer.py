#!/usr/bin/env python3
"""Exercise the liveness-gap fix directly.

By the time this step calls amend(), producer.sh has already completed and data.txt is
BUILT. Under the old single-bucket rescheduled_info scheme, the dependency edge to
data.txt would be created only now, *after* producer.sh's own File.completed() already
ran --- so nothing would ever call mark_pending() on this step again, and it would sit
in PENDING forever. With the unavailable_inputs/unfresh_inputs split, this step's
freshness-only reschedule self-resolves on its own next dispatch, with no push needed.
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

# Comfortable margin for the director to register data.txt as BUILT (see
# relax_amend_success/consumer.py for why this matters).
time.sleep(0.2)

content = data_path.read_text()
amend(inp=["data.txt"])
print(f"consumer read: {content.strip()}")
