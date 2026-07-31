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

# The plan step must fail with a message naming the root-tree restriction.
grep -F "cannot be the project root" .stepup/fail.log

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
