#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example with token resource explicitly set to zero capacity.
export STEPUP_RESOURCES="token:0"
stepup boot -n 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# The plan step succeeded; the resource-blocked steps stayed pending (not failed).
[[ -f .stepup/warning.log ]] || exit 1
[[ ! -f .stepup/fail.log ]] || exit 1
