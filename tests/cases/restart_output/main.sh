#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the plan.
stepup -e -w 1 plan.py & # > current_stdout_01.txt &
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01")
join()
EOD

# Wait for background processes, if any.
wait $(jobs -p)

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f original.txt ]] || exit -1
[[ -f copy.txt ]] || exit -1

# Restart StepUp after removing the output.
rm copy.txt
stepup -e -w 1 plan.py & # > current_stdout_02.txt &
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_02")
join()
EOD

# Wait for background processes, if any.
wait $(jobs -p)

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f original.txt ]] || exit -1
[[ -f copy.txt ]] || exit -1
