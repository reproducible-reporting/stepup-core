#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run with the initial subs.txt.
echo inp1 > inp1.txt
echo conv1.txt > subs1.txt
stepup -e -w 1 plan.py & # > current_stdout.txt &

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

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp1.txt ]] || exit 1
[[ -f subs1.txt ]] || exit 1
[[ -f subs2.txt ]] || exit 1
grep conv1.txt subs2.txt
[[ -f subs3.txt ]] || exit 1
grep conv1.txt subs3.txt

# Change subs.txt and rerun.
rm inp1.txt
echo inp2 > inp2.txt
echo conv2.txt > subs1.txt
python3 - << EOD
from stepup.core.interact import *
watch_update("subs1.txt")
watch_delete("inp1.txt")
run()
wait()
graph("current_graph_02")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f inp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
[[ -f subs1.txt ]] || exit 1
[[ -f subs2.txt ]] || exit 1
grep conv2.txt subs2.txt
[[ -f subs3.txt ]] || exit 1
grep conv2.txt subs3.txt
