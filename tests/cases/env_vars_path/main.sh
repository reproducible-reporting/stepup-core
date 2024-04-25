#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the example
export ENV_VAR_TEST_STEPUP_PREFIX="README"
stepup -e -w 1 plan.py & # > current_stdout_01.txt &

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01.txt")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f README-stdout.txt ]] || exit -1
[[ -f README-stderr.txt ]] || exit -1

# Wait for background processes, if any.
wait $(jobs -p)

# Restart the example with a different variable
export ENV_VAR_TEST_STEPUP_PREFIX="FOO"
stepup -e -w 1 plan.py & # > current_stdout_02.txt &

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_02.txt")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ ! -f README-stdout.txt ]] || exit -1
[[ ! -f README-stderr.txt ]] || exit -1
[[ -f FOO-stdout.txt ]] || exit -1
[[ -f FOO-stderr.txt ]] || exit -1

# Wait for background processes, if any.
wait $(jobs -p)
