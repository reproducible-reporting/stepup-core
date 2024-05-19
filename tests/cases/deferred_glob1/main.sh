#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the example
export ENV_VAR_TEST_STEPUP_INP="static/sub/foo.txt"
stepup -w 1 plan.py & # > current_stdout_01.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f static/sub/foo.txt ]] || exit -1
[[ -f copy.txt ]] || exit -1
grep foo copy.txt

# Wait for background processes, if any.
wait

# Restart the example with a different input
export ENV_VAR_TEST_STEPUP_INP="static/sub/bar.txt"
rm -r .stepup/logs
stepup -w 1 plan.py & # > current_stdout_02.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_02")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f static/sub/foo.txt ]] || exit -1
[[ -f copy.txt ]] || exit -1
grep bar copy.txt

# Wait for background processes, if any.
wait
