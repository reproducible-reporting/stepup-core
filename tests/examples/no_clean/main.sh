#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
cp plan1.py plan.py
stepup boot -n 1 -w & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f test1.txt ]] || exit 1

# Change the plan to a different one and restart with --no-clean
cp plan2.py plan.py
rm .stepup/*.log
stepup boot -n 1 -w --no-clean & # > current_stdout2.txt &

# Wait for watch phase.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f test1.txt ]] || exit 1
[[ -f test2.txt ]] || exit 1
