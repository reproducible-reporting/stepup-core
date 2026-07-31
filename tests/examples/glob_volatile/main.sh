#!/usr/bin/env -S bash -x
source ../example.rc

# vol.txt already exists on disk, as if left over from a previous run.
echo stale > vol.txt

# Run the example
sb -j 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# Eager check (a) also rejects a match against a VOLATILE output.
grep -F "Glob pattern (*.txt) registered by step (./plan.py) matches (vol.txt), which step (touch vol.txt) builds." .stepup/fail.log

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
