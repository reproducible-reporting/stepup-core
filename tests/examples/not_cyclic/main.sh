#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example. The step declares input.txt as static and then uses it as input,
# which is allowed and must not be flagged as a cyclic dependency.
echo "hello" > input.txt
sb -j 1 -w & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f input.txt ]] || exit 1
grep hello output.txt

# Restore the source file for clean local reruns.
echo hello > input.txt
