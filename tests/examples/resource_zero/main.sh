#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example with token resource explicitly set to zero capacity.
export STEPUP_BUILD_RESOURCES="token:0"
sb -j 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# The plan step succeeded; the resource-blocked steps stayed pending (not failed).
[[ -f .stepup/warning.log ]] || exit 1
[[ ! -f .stepup/fail.log ]] || exit 1
