#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
export STEPUP_ROOT="${PWD}/project"
export STEPUP_PATH_FILTER="+${PWD}/pkgs"
export PYTHONPATH="${PWD}/pkgs:${PYTHONPATH}"
cp pkgs/helper1.py pkgs/helper.py
stepup boot -n 1 -w & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph ../current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f project/plan.py ]] || exit 1
[[ -f project/worker.py ]] || exit 1
[[ -f project/worker_out.json ]] || exit 1
grep 'Helper version 1' project/worker_out.json


# Change helper.py to helper2.py and run the example again.
cp pkgs/helper2.py pkgs/helper.py
stepup boot -n 1 -w & # > current_stdout2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph ../current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f project/plan.py ]] || exit 1
[[ -f project/worker.py ]] || exit 1
[[ -f project/worker_out.json ]] || exit 1
grep 'Helper version 2' project/worker_out.json
