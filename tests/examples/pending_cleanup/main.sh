#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
echo "created elsewhere" > hello.txt
echo "created elsewhere, but will be overwritten and deleted" > bye.txt
stepup -w -n 1 plan.py & # > current_stdout1.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph1")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f hello.txt ]] || exit 1
[[ -f bye.txt ]] || exit 1
grep elsewhere hello.txt
grep soon bye.txt

# Call cleanup to remove all outputs
cleanup -vv . > current_cleanup.txt

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f hello.txt ]] || exit 1
[[ ! -f bye.txt ]] || exit 1
grep elsewhere hello.txt

# Restart without changes
stepup -w -n 1 plan.py > current_stdout2.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph2")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f hello.txt ]] || exit 1
[[ -f bye.txt ]] || exit 1
grep elsewhere hello.txt
grep soon bye.txt
# The file hello.txt was AWAITED, so changes to this file are not relevant.
[[ $(grep -c "UPDATED │ hello.txt" current_stdout2.txt ) -eq 0 ]] || exit 1
