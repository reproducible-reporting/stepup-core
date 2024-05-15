#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the example
echo "hello" > input.txt
stepup -w 1 plan.py & # > current_stdout.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01")
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f input.txt ]] || exit -1
grep hello output.txt

# Change the input and check that the step reruns without pending dependencies
echo "bye" > input.txt
python3 - << EOD
from stepup.core.interact import *
watch_update("input.txt")
run()
wait()
graph("current_graph_02")
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f input.txt ]] || exit -1
grep bye output.txt

# Touch input (no changes) and check that the step reruns without pending dependencies
touch input.txt
rm output.txt
python3 - << EOD
from stepup.core.interact import *
watch_update("input.txt")
watch_delete("output.txt")
run()
wait()
graph("current_graph_03")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f input.txt ]] || exit -1
grep bye output.txt

# Wait for background processes, if any.
wait
