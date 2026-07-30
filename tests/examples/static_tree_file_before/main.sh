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

# The error must name both paths and state the rule.
grep -F "Static tree" .stepup/fail.log
grep -F "src/foo.txt" .stepup/fail.log

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f src/foo.txt ]] || exit 1
