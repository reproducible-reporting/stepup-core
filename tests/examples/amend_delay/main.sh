#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Prepare inputs
echo "Something old" > inp0.txt
echo "First inp1.txt" > inp1.txt
# Prepare a stray output file, which should get overwritten.
# It is intended to confuse the rescheduling.
echo "Stray output" > tmp1.txt

# Run the plan.
stepup -w -e -n 1 & # > current_stdout.txt &

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

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp0.txt ]] || exit 1
[[ -f inp1.txt ]] || exit 1
[[ -f tmp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
[[ -f log.txt ]] || exit 1

# Remove input and rerun the plan.
rm inp2.txt
python3 - << EOD
from stepup.core.interact import *
watch_delete("inp2.txt")
run()
wait()
graph("current_graph2")
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp0.txt ]] || exit 1
[[ -f inp1.txt ]] || exit 1
[[ -f tmp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
[[ -f log.txt ]] || exit 1

# Replace input and rerun the plan.
echo "Something new" > inp0.txt
echo "Second inp1.txt" > inp1.txt
python3 - << EOD
from stepup.core.interact import *
watch_update("inp0.txt")
watch_update("inp1.txt")
run()
wait()
graph("current_graph3")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp0.txt ]] || exit 1
[[ -f inp1.txt ]] || exit 1
[[ -f tmp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
[[ -f log.txt ]] || exit 1
