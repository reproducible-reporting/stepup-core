#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the first plan.
stepup -e -w 1 plan_01.py & # > current_stdout_01.txt &

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
[[ -f plan_01.py ]] || exit -1
[[ -f copy_01.txt ]] || exit -1
[[ -f copy_both1.txt ]] || exit -1
[[ -f source_both.txt ]] || exit -1
[[ -f source_01.txt ]] || exit -1

# second with a different plan.
echo
rm -r .stepup/logs
stepup -e -w 1 plan_02.py & # > current_stdout_02.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Restart StepUp.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_02")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan_01.py ]] || exit -1
[[ -f plan_02.py ]] || exit -1

# Static files should never be removed.
[[ -f source_both.txt ]] || exit -1
[[ -f source_01.txt ]] || exit -1
[[ -f source_02.txt ]] || exit -1

# Only the output file of the second should remain.
[[ ! -f copy_01.txt ]] || exit -1
[[ ! -f copy_both1.txt ]] || exit -1
[[ -f copy_02.txt ]] || exit -1
[[ -f copy_both2.txt ]] || exit -1
