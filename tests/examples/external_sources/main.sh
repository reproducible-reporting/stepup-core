#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
export STEPUP_ROOT="${PWD}/project"
export STEPUP_EXTERNAL_SOURCES="${PWD}/pkgs"
export PYTHONPATH="${PWD}/pkgs:${PYTHONPATH}"
cp pkgs/helper1.py pkgs/helper.py
stepup -w -n 1 & # > current_stdout1.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("../current_graph1")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f project/plan.py ]] || exit 1
[[ -f project/worker.py ]] || exit 1
[[ -f project/worker_out.json ]] || exit 1
grep 'Helper version 1' project/worker_out.json


# Change helper.py to helper2.py and run the example again.
cp pkgs/helper2.py pkgs/helper.py
stepup -w -n 1 & # > current_stdout2.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("../current_graph2")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f project/plan.py ]] || exit 1
[[ -f project/worker.py ]] || exit 1
[[ -f project/worker_out.json ]] || exit 1
grep 'Helper version 2' project/worker_out.json
