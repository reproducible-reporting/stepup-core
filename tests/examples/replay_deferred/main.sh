#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the initial plan.
echo hello > inp.txt
cp plan1.py plan.py
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

# Take work.py out of the plan.
cp plan2.py plan.py

# Remove input and rerun the plan, should not do much.
python3 - << EOD
from stepup.core.interact import *
watch_update("plan.py")
run()
wait()
graph("current_graph2")
EOD

# Check the result, should not be affected.
grep hello out.txt

# Restor work.ppy in the plan.
cp plan1.py plan.py

# Remove input and rerun the plan, should not do much.
python3 - << EOD
from stepup.core.interact import *
watch_update("plan.py")
run()
wait()
graph("current_graph3")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp.txt ]] || exit 1
[[ -f out.txt ]] || exit 1
