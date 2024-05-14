#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the initial plan.
cp first.txt input.txt
stepup -w 1 plan.py & # > current_stdout.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Initial graph
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01")
EOD

# Remove input and rerun the plan.
rm input.txt
python3 - << EOD
from stepup.core.interact import *
watch_delete("input.txt")
run()
wait()
graph("current_graph_02")
EOD

# Replace input and rerun the plan.
cp second.txt input.txt
python3 - << EOD
from stepup.core.interact import *
watch_update("input.txt")
run()
wait()
graph("current_graph_03")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f input.txt ]] || exit -1
[[ -f output.txt ]] || exit -1
