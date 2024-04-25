#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run with the initial subs.txt.
echo inp1 > inp1.txt
echo inp1.txt > subs.txt
stepup -e -w 1 plan.py & # > current_stdout.txt &

# Initial graph
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01.txt")
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f inp1.txt ]] || exit -1
[[ -f copy.txt ]] || exit -1
grep inp1 copy.txt

# Change subs.txt and rerun.
rm inp1.txt
echo inp2 > inp2.txt
echo inp2.txt > subs.txt
python3 - << EOD
from stepup.core.interact import *
watch_add("subs.txt")
watch_del("inp1.txt")
run()
wait()
graph("current_graph_02.txt")
join()
EOD

# Wait for background processes, if any.
wait $(jobs -p)

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ ! -f inp1.txt ]] || exit -1
[[ -f inp2.txt ]] || exit -1
[[ -f copy.txt ]] || exit -1
grep inp2 copy.txt
