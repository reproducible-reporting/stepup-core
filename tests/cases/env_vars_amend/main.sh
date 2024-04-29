#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Prepare some variables
export ENV_VAR_TEST_STEPUP_SDASFD="AAAA"

# Run the example
stepup -e -w 1 plan.py & # > current_stdout_01.txt &

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
grep AAAA output.txt

# Wait for background processes, if any.
wait $(jobs -p)

# Rerstart with changed variable
export ENV_VAR_TEST_STEPUP_SDASFD="BBBB"
stepup -e -w 1 plan.py & # > current_stdout_02.txt &
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_02")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
grep BBBB output.txt

# Wait for background processes, if any.
wait $(jobs -p)
