#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
export SUB="sub"
export DELAYED='${HERE}/foo.txt'
stepup boot -j 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f sub/plan.py ]] || exit 1

# Wait for background processes, if any.
wait
