#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
export STEPUP_BOOT_RESOURCES="transform:1"
stepup boot -j 2 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f r.txt ]] || exit 1
[[ ! -f u.txt ]] || exit 1
[[ ! -f .stepup/fail.log ]] || exit 1
