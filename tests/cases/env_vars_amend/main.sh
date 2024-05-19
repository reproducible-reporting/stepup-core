#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Prepare some variables
export ENV_VAR_TEST_STEPUP_SDASFD="AAAA"

# Run the example
stepup -e -w 1 plan.py & # > current_stdout_01.txt &

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
grep AAAA output.txt

# Wait for background processes, if any.
wait

# Rerstart with changed variable
export ENV_VAR_TEST_STEPUP_SDASFD="BBBB"
rm -r .stepup/logs
stepup -e -w 1 plan.py & # > current_stdout_02.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

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
wait
