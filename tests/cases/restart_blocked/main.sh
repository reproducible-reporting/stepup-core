#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the initial plan.
cp plan_blocked.py plan.py
stepup -w 1 plan.py & # > current_stdout_01.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# First graph
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01")
join()
EOD

# Wait for background processes, if any.
wait

[[ -f initial.txt ]] || exit -1
[[ -f input.txt ]] || exit -1
[[ ! -f output.txt ]] || exit -1
[[ ! -f final.txt ]] || exit -1

# Modify a few things and restart
cp plan_unblocked.py plan.py
rm -r .stepup/logs
stepup -w 1 plan.py & # > current_stdout_02.txt &

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

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f initial.txt ]] || exit -1
[[ -f input.txt ]] || exit -1
[[ -f output.txt ]] || exit -1
[[ -f final.txt ]] || exit -1
