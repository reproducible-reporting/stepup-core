#!/usr/bin/env -S bash -x
source ../example.rc

# Prepare symlinked package directory.
ln -s pkgs_a pkgs_b

# Run the example
export STEPUP_ROOT="${PWD}/project"
export STEPUP_PATH_FILTER="+${PWD}/pkgs_a"
export PYTHONPATH="${PWD}/pkgs_b:${PYTHONPATH}"
sb -j 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph ../current_graph
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f project/plan.py ]] || exit 1
[[ -f project/worker.py ]] || exit 1
[[ -f project/worker_out.json ]] || exit 1
grep 'Helper version 3' project/worker_out.json
grep 'consumes   file:../pkgs_a/helper.py \[amended\]' current_graph.txt
