#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the example
stepup -w 1 plan.py & # > current_stdout.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f first.txt ]] || exit -1
[[ -f second.txt ]] || exit -1

# Start stepup without checking expected output because watchdog file
# order is not reproducible.
rm -r .stepup/logs
stepup -w 1 plan.py &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Wait for watch phase.
python3 - << EOD
from stepup.core.interact import *
wait()
EOD

# Unset STEPUP_DIRECTOR_SOCKET because cleanup should work without it.
unset STEPUP_DIRECTOR_SOCKET

# Test cleanup that has no effect
cleanup plan.py

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f first.txt ]] || exit -1
[[ -f second.txt ]] || exit -1

# Test cleanup that removes first and second
cleanup ./

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ ! -f first.txt ]] || exit -1
[[ ! -f second.txt ]] || exit -1

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Exit stepup
python3 - << EOD
from stepup.core.interact import *
join()
EOD

# Wait for background processes, if any.
wait
