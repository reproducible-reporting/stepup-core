#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the initial plan.
cp plan_full.py plan.py
cp backup.txt orig.txt
stepup -w 1 plan.py & # > current_stdout.txt &

# First graph
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01")
EOD

# Modify a few things and rerun
cp plan_trimmed.py plan.py
rm orig.txt
python3 - << EOD
from stepup.core.interact import *
watch_update("plan.py")
watch_delete("orig.txt")
run()
wait()
graph("current_graph_02")
join()
EOD

# Wait for background processes, if any.
wait $(jobs -p)

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f copy1.txt ]] || exit -1
[[ -f copy2.txt ]] || exit -1
