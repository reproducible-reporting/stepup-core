#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
sb -j 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# Eager check (b): the same message text as glob_output_before, independent of order.
grep -F "Glob pattern (*.txt) registered by step (./plan.py) matches (out.txt), which step (touch out.txt) builds." .stepup/fail.log

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f out.txt ]] || exit 1
