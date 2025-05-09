#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the initial plan.
echo hello > inp.txt
stepup boot -n 1 -w & # > current_stdout.txt &

# Initial graph
stepup wait
stepup graph current_graph1

# Check the result.
grep hello out.txt

# Remove input and rerun the plan, should not do much.
rm inp.txt
stepup watch-delete inp.txt
stepup run
stepup wait
stepup graph current_graph2

# Check the result, should not be affected.
grep hello out.txt

# Restore the input and rerun, this should result in a replay
echo hello > inp.txt
stepup watch-update inp.txt
stepup run
stepup wait
stepup graph current_graph3

# Check the result, should not be affected.
grep hello out.txt

# Change the input and rerun, this should rerun the step.
echo bye > inp.txt
stepup watch-update inp.txt
stepup run
stepup wait
stepup graph current_graph4
stepup join

# Wait for background processes, if any.
wait

# Check the result, should not be affected.
grep bye out.txt

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp.txt ]] || exit 1
[[ -f out.txt ]] || exit 1
