#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the initial plan.
stepup -w -n 1 > current_stdout.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Initial graph
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph1")
EOD

# Modify the volatile output file.
echo spam spam > vol.txt; sleep 0.5
python3 - << EOD
from stepup.core.interact import *
run()
wait()
graph("current_graph2")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
# The file inp.txt was AWAITED, so changes to this file are not relevant.
[[ $(grep -c "UPDATED │ vol.txt" current_stdout.txt ) -eq 0 ]] || exit 1
