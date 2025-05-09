#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
echo "created elsewhere" > test1.txt
stepup boot -n 1 -w & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f test1.txt ]] || exit 1
[[ -f test2.txt ]] || exit 1

# Call stepup clean to remove all outputs
stepup clean . > current_cleanup.txt

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f test1.txt ]] || exit 1
[[ ! -f test2.txt ]] || exit 1

# Restart without changes
stepup boot -n 1 -w > current_stdout2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f test1.txt ]] || exit 1
[[ -f test2.txt ]] || exit 1
# The file test1.txt was AWAITED, so changes to this file are not relevant for StepUp.
[[ $(grep -c "UPDATED │ test1.txt" current_stdout2.txt ) -eq 0 ]] || exit 1
