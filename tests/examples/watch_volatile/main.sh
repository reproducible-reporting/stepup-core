#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the initial plan.
stepup boot -n 1 -w > current_stdout.txt &

# Initial graph
stepup wait
stepup graph current_graph1

# Modify the volatile output file.
echo spam spam > vol.txt; sleep 0.5
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
# The file inp.txt was AWAITED, so changes to this file are not relevant.
[[ $(grep -c "UPDATED │ vol.txt" current_stdout.txt ) -eq 0 ]] || exit 1
