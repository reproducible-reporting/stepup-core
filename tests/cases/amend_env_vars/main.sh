#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the example
export INP_VAR_TEST_STEPUP_FOO="foo"
export INP_VAR_TEST_STEPUP_BAR="bar"
stepup -w 1 plan.py & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f foo.txt ]] || exit -1
[[ -f bar.txt ]] || exit -1
[[ -f bar.log ]] || exit -1

# Wait for background processes, if any.
wait $(jobs -p)
