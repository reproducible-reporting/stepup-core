#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the initial plan.
cp plan_01.py plan.py
stepup -w 1 plan.py & # > current_stdout.txt &

# Initial graph
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01.txt")
EOD

# Remove plan and rerun.
rm plan.py
python3 - << EOD
from stepup.core.interact import *
watch_delete("plan.py")
run()
wait()
graph("current_graph_02.txt")
EOD

# Check files that are expected to be present and/or missing.
# Files should not be removed because of pending steps:
[[ -f copy.txt ]] || exit -1
[[ -f another.txt ]] || exit -1

# Replace plan and rerun.
cp plan_02.py plan.py
python3 - << EOD
from stepup.core.interact import *
watch_update("plan.py")
run()
wait()
graph("current_graph_03.txt")
join()
EOD

# Wait for background processes, if any.
wait $(jobs -p)

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f original.txt ]] || exit -1
[[ ! -f copy.txt ]] || exit -1
[[ -f another.txt ]] || exit -1
[[ -f between.txt ]] || exit -1
