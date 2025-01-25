#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the initial plan.
echo hello > inp.txt
stepup -w -n 1 plan.py & # > current_stdout.txt &

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

# Check the result.
grep hello out.txt

# Remove input and rerun the plan, should not do much.
rm inp.txt
python3 - << EOD
from stepup.core.interact import *
watch_delete("inp.txt")
run()
wait()
graph("current_graph2")
EOD

# Check the result, should not be affected.
grep hello out.txt

# Restore the input and rerun, this should result in a replay
echo hello > inp.txt
python3 - << EOD
from stepup.core.interact import *
watch_update("inp.txt")
run()
wait()
graph("current_graph3")
EOD

# Check the result, should not be affected.
grep hello out.txt

# Change the input and rerun, this should rerun the step.
echo bye > inp.txt
python3 - << EOD
from stepup.core.interact import *
watch_update("inp.txt")
run()
wait()
graph("current_graph4")
join()
EOD

# Wait for background processes, if any.
wait

# Check the result, should not be affected.
grep bye out.txt

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp.txt ]] || exit 1
[[ -f out.txt ]] || exit 1
