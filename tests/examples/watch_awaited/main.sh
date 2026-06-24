#!/usr/bin/env -S bash -x
source ../example.rc

# Run the initial plan.
sb -j 1 -w > current_stdout.txt &

# Initial graph
stepup wait
stepup graph current_graph1

# Create the input file.
touch inp.txt; sleep 0.5
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
# The file inp.txt was AWAITED, so changes to this file are not relevant.
[[ $(grep -c "UPDATED │ inp.txt" current_stdout.txt ) -eq 0 ]] || exit 1
