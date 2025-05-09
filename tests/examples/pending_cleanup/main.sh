#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
echo "created elsewhere" > hello.txt
echo "created elsewhere, but will be overwritten and deleted" > bye.txt
stepup boot -n 1 -w & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait
ls .stepup/
# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f hello.txt ]] || exit 1
[[ -f bye.txt ]] || exit 1
grep elsewhere hello.txt
grep soon bye.txt

# Call stepup clean to remove all outputs
stepup clean . > current_cleanup.txt

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f hello.txt ]] || exit 1
[[ ! -f bye.txt ]] || exit 1
grep elsewhere hello.txt

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
[[ -f hello.txt ]] || exit 1
[[ -f bye.txt ]] || exit 1
grep elsewhere hello.txt
grep soon bye.txt
# The file hello.txt was AWAITED, so changes to this file are not relevant.
[[ $(grep -c "UPDATED │ hello.txt" current_stdout2.txt ) -eq 0 ]] || exit 1
