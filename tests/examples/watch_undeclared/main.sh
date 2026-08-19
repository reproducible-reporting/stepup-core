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
# The node for inp.txt is detached (UNDECLARED): nothing declares the file,
# so change_is_relevant falls through to the glob patterns, of which there are none.
[[ $(grep -c "UPDATED │ inp.txt" current_stdout.txt ) -eq 0 ]] || exit 1
