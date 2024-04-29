#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the example
export ENV_VAR_TEST_STEPUP_IDX="3"
stepup -e -w 1 plan.py & # > current_stdout_a.txt &

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_a")
EOD

# Rerun without changes
python3 - << EOD
from stepup.core.interact import *
run()
wait()
graph("current_graph_b")
join()
EOD

# Wait for background processes, if any.
wait $(jobs -p)

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f converted_03.txt ]] || exit -1
[[ -f used.txt ]] || exit -1
grep raw_03 used.txt
[[ ! -f converted_01.txt ]] || exit -1
[[ ! -f converted_02.txt ]] || exit -1
[[ ! -f converted_04.txt ]] || exit -1

# Restart with a different environment variables
export ENV_VAR_TEST_STEPUP_IDX="1"
stepup -e -w 1 plan.py & # > current_stdout_b.txt &

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_c")
join()
EOD

# Wait for background processes, if any.
wait $(jobs -p)

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f converted_01.txt ]] || exit -1
[[ -f used.txt ]] || exit -1
grep raw_01 used.txt
[[ ! -f converted_02.txt ]] || exit -1
[[ ! -f converted_03.txt ]] || exit -1
[[ ! -f converted_04.txt ]] || exit -1
