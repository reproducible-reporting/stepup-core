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
[[ -f data/a/x.txt ]] || exit 1
[[ -f data/b/y.txt ]] || exit 1

# All three declarations cover the same tree, so only one static tree node may exist.
[[ "$(grep -c '^st:' current_graph.txt)" -eq 1 ]] || exit 1
