#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
export STEPUP_ROOT="${PWD}/source"
export PUBLIC="../public"
sb -j 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph ../current_graph
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f source/plan.py ]] || exit 1
[[ -f source/www/plan.py ]] || exit 1
[[ -f source/www/index.md ]] || exit 1
[[ -f public/www/index.md ]] || exit 1
