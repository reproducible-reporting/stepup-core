#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
export ENV_VAR_TEST_STEPUP_INP="static/sub/foo.txt"
stepup boot -n 1 -w & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f static/sub/foo.txt ]] || exit 1
[[ -f copy.txt ]] || exit 1
grep foo copy.txt

# Wait for background processes, if any.
wait

# Restart the example with a different input
export ENV_VAR_TEST_STEPUP_INP="static/sub/bar.txt"
rm .stepup/*.log
stepup boot -n 1 -w & # > current_stdout2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f static/sub/foo.txt ]] || exit 1
[[ -f copy.txt ]] || exit 1
grep bar copy.txt
