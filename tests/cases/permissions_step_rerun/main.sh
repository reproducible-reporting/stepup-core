#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the plan with non-executable step.py.
chmod -x step.py
stepup -w 1 plan.py & # > current_stdout.txt &

# Wait and get graph.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01.txt")
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f step.py ]] || exit -1
[[ ! -f message.txt ]] || exit -1

# Rerun the plan with executable step.py.
chmod +x step.py

# Wait and get graph.
python3 - << EOD
from stepup.core.interact import *
watch_update("step.py")
run()
wait()
graph("current_graph_02.txt")
join()
EOD

# Wait for background processes, if any.
wait $(jobs -p)

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f step.py ]] || exit -1
[[ -f message.txt ]] || exit -1
