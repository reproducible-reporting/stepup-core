#!/usr/bin/env -S bash -x
source ../example.rc

# out.txt already exists on disk, as if left over from a previous run.
echo stale > out.txt

# Run the example
sb -j 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# Eager check (a): the pattern matches a file that another step already builds.
grep -F "Glob pattern (*.txt) registered by step (./plan.py) matches (out.txt), which step (touch out.txt) builds." .stepup/fail.log

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
