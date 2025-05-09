#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
export ENV_VAR_TEST_STEPUP_PREFIX="README"
stepup boot -n 1 -w -e & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f README-stdout.txt ]] || exit 1
[[ -f README-stderr.txt ]] || exit 1

# Restart the example with a different variable
export ENV_VAR_TEST_STEPUP_PREFIX="FOO"
rm .stepup/*.log
stepup boot -n 1 -w -e & # > current_stdout2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f README-stdout.txt ]] || exit 1
[[ ! -f README-stderr.txt ]] || exit 1
[[ -f FOO-stdout.txt ]] || exit 1
[[ -f FOO-stderr.txt ]] || exit 1
