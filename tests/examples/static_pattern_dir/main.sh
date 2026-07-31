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
grep hello out.txt

# Both matched directories must be registered as static trees.
grep -qFx "st:data/a/" current_graph.txt
grep -qFx "st:data/b/" current_graph.txt
# The directory match itself must not create a spurious file node in the graph.
! grep -qFx "file:data/a/" current_graph.txt
! grep -qFx "file:data/b/" current_graph.txt
