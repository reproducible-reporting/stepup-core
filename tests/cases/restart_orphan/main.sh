#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the plan.
stepup -e -w 1 plan.py & # > current_stdout_01.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Run StepUp for a first time.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f foo.txt ]] || exit -1
[[ -f bar.txt ]] || exit -1

# Run the plan.
rm -r .stepup/logs
stepup -e -w 1 plan.py & # > current_stdout_02.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Restart StepUp without making changes.
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
[[ -f foo.txt ]] || exit -1
[[ -f bar.txt ]] || exit -1
