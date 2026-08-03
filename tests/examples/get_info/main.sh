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

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f out0.txt ]] || exit 1
[[ -f sub/out1.txt ]] || exit 1
[[ -f out2.txt ]] || exit 1
[[ -f sub/vol0.txt ]] || exit 1
[[ -f vol1.txt ]] || exit 1
[[ -f vol2.txt ]] || exit 1
