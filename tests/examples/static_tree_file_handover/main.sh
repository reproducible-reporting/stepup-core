#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
sb -j 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f src/foo.txt ]] || exit 1
[[ -f copy.txt ]] || exit 1
grep foo copy.txt

# src/foo.txt must end up owned by the tree, not by the plan step that first declared it.
grep -A3 "^file:src/foo.txt$" current_graph.txt | grep -qF "creator   st:src/"
