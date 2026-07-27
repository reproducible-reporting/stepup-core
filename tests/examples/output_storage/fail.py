#!/usr/bin/env python3
# Output is stored even when the step fails (storage is independent of success).
# Raising (rather than sys.exit) yields the same captured streams on both the
# forkserver and plain-subprocess execution paths.
print("fail-output-line")
# Burn a clearly measurable amount of CPU before failing, so that checkdb.py can assert that
# a step failing with an uncaught exception still records its resource usage: the closing
# getrusage() snapshot in _forkserver_entry has to be taken in a `finally`, since an uncaught
# exception skips the tail of the `try` body.
sum(range(2000000))
raise RuntimeError("deliberate failure")
