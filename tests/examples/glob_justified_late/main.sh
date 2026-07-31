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

# No warning: sub/plan.py's static() declaration, which runs after the root's
# glob(), justifies the match by the time the end-of-phase check runs.
[[ ! -f .stepup/warning.log ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f sub/plan.py ]] || exit 1
[[ -f sub/a.txt ]] || exit 1
[[ -f sub/b.txt ]] || exit 1
