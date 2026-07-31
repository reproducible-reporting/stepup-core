#!/usr/bin/env -S bash -x
source ../example.rc

# Prepare static inputs
echo "First input" > inp1.txt
echo "Second input" > inp2.txt

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
grep First out1.txt
grep Second out2.txt

# static(ng) must not register the pattern a second time.
[[ "$(grep -Fc 'nglob = inp*.txt' current_graph.txt)" -eq 1 ]] || exit 1
