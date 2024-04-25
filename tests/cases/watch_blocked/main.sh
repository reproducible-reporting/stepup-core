#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the initial plan.
cp plan_blocked.py plan.py
stepup -w 1 plan.py & # > current_stdout.txt &

# First graph
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01.txt")
EOD

[[ -f initial.txt ]] || exit -1
[[ -f input.txt ]] || exit -1
[[ ! -f output.txt ]] || exit -1
[[ ! -f final.txt ]] || exit -1

# Modify a few things and rerun
cp plan_unblocked.py plan.py
python3 - << EOD
from stepup.core.interact import *
watch_add("plan.py")
run()
wait()
graph("current_graph_02.txt")
join()
EOD

# Wait for background processes, if any.
wait $(jobs -p)

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f initial.txt ]] || exit -1
[[ -f input.txt ]] || exit -1
[[ -f output.txt ]] || exit -1
[[ -f final.txt ]] || exit -1
