#!/usr/bin/env python3
import contextlib

from stepup.core.api import InputNotFoundError, amend

# Deliberately violates amend()'s documented contract ("let this exception
# propagate; do not catch it") to test that a swallowed exception still
# produces a full FAIL report, not just a bare FAIL with no explanation.
with contextlib.suppress(InputNotFoundError):
    amend(inp=["never.txt"])
print("never.txt is now available.")
