#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
export INP_VAR_TEST_STEPUP_FOO="foo"
export INP_VAR_TEST_STEPUP_BAR="bar"
stepup boot -n 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f foo.txt ]] || exit 1
[[ -f bar.txt ]] || exit 1
[[ -f bar.log ]] || exit 1
