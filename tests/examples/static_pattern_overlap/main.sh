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
[[ -f data/sub/a.txt ]] || exit 1

# Both patterns match data/sub/a.txt, so it may appear only once in the graph.
[[ "$(grep -c '^file:data/sub/a.txt$' current_graph.txt)" -eq 1 ]] || exit 1
