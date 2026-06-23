#!/usr/bin/env python3
# Output is stored even when the step fails (storage is independent of success).
# Raising (rather than sys.exit) yields the same captured streams on both the
# forkserver and plain-subprocess execution paths.
print("fail-output-line")
raise RuntimeError("deliberate failure")
