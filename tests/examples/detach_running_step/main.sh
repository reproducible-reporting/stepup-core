#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
sb -j 3 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f provided.txt ]] || exit 1
[[ -f trigger_work1.txt ]] || exit 1
[[ -f trigger_sub.txt ]] || exit 1
[[ -f trigger_work2.txt ]] || exit 1
