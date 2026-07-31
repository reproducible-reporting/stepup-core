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

# The error must name the recursive wildcard and point at the static tree alternative.
grep -F "recursive" .stepup/fail.log
grep -F "static('src/')" .stepup/fail.log

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f sub/data.txt ]] || exit 1
