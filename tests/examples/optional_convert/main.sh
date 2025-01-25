#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
export ENV_VAR_TEST_STEPUP_IDX="3"
stepup -w -e -n 1 plan.py & # > current_stdout1.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph1")
EOD

# Rerun without changes
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
[[ -f converted3.txt ]] || exit 1
[[ -f used.txt ]] || exit 1
grep raw3 used.txt
[[ ! -f converted1.txt ]] || exit 1
[[ ! -f converted2.txt ]] || exit 1
[[ ! -f converted4.txt ]] || exit 1

# Restart with a different environment variables
export ENV_VAR_TEST_STEPUP_IDX="1"
rm .stepup/*.log
stepup -w -e -n 1 plan.py & # > current_stdout2.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph3")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f converted1.txt ]] || exit 1
[[ -f used.txt ]] || exit 1
grep raw1 used.txt
[[ ! -f converted2.txt ]] || exit 1
[[ ! -f converted3.txt ]] || exit 1
[[ ! -f converted4.txt ]] || exit 1


# Restart with the original environment variable.
export ENV_VAR_TEST_STEPUP_IDX="3"
rm .stepup/*.log
stepup -w -e -n 1 plan.py & # > current_stdout3.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph4")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f converted3.txt ]] || exit 1
[[ -f used.txt ]] || exit 1
grep raw3 used.txt
[[ ! -f converted1.txt ]] || exit 1
[[ ! -f converted2.txt ]] || exit 1
[[ ! -f converted4.txt ]] || exit 1
