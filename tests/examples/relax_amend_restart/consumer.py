#!/usr/bin/env python3
"""Read data.txt (once available) and amend it.

Declares an ordinary static input (trigger.txt) too, so it can be forced to re-run in
a second director invocation without touching data.txt or its producer at all.
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
# relax_amend_success/consumer.py for why this matters). Only relevant for the very
# first invocation, where producer.sh and this step race; irrelevant afterward.
time.sleep(0.2)

content = data_path.read_text()
amend(inp=["data.txt"])
print(f"consumer read: {content.strip()}")
