#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the initial plan.
cp -a plan_01.py plan.py
stepup -w 1 plan.py & # > current_stdout.txt &

# Initial graph
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01.txt")
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f first.txt ]] || exit -1
[[ -f final.txt ]] || exit -1

# Modify the plan.py script and rerun with the modified plan.py.
cp -a plan_02.py plan.py
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
[[ ! -f first.txt ]] || exit -1
[[ -f second.txt ]] || exit -1
[[ -f final.txt ]] || exit -1
