#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the plan with specific permissions on the input.
chmod +x input.txt
stepup -w 1 plan.py & # > current_stdout.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Wait and get graph.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01")
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f input.txt ]] || exit -1
[[ -f output.txt ]] || exit -1
stat input.txt | grep 'Access: (0755/-rwxr-xr-x)'
stat output.txt | grep 'Access: (0755/-rwxr-xr-x)'

# Rerun the plan with different permissions for the input.
chmod -x input.txt

# Wait and get graph.
python3 - << EOD
from stepup.core.interact import *
watch_update("input.txt")
run()
wait()
graph("current_graph_02")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f input.txt ]] || exit -1
[[ -f output.txt ]] || exit -1
stat input.txt | grep 'Access: (0644/-rw-r--r--)'
stat output.txt | grep 'Access: (0644/-rw-r--r--)'
