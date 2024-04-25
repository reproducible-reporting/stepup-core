#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the example
stepup -w 1 plan.py & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph.txt")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f sub/plan.py ]] || exit -1
[[ -f sub/inp1.txt ]] || exit -1
[[ -f sub/inp2.txt ]] || exit -1
[[ -f sub/out1.txt ]] || exit -1
[[ -f sub/out2.txt ]] || exit -1
grep one sub/out1.txt
grep two sub/out2.txt

# Wait for background processes, if any.
wait $(jobs -p)
