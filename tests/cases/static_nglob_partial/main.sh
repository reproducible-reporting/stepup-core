#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Prepare static inputs
echo "Not" > head_ignored.txt
echo "matching" > tail_ignored.txt
echo "hx" > head_x.txt
echo "tx" > tail_x.txt
echo "hy" > head_y.txt

# Run the example
stepup -e -w 1 plan.py & # > current_stdout_a.txt &

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
[[ ! -f paste_ignored.txt ]] || exit -1
grep 'hx tx' paste_x.txt
[[ ! -f paste_y.txt ]] || exit -1

# Modify nglob results and rerun
echo "ty" > tail_y.txt
echo "hz" > head_z.txt
python3 - << EOD
from stepup.core.interact import *
watch_update("tail_y.txt")
watch_update("head_z.txt")
run()
wait()
graph("current_graph_02")
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ ! -f paste_ignored.txt ]] || exit -1
grep 'hx tx' paste_x.txt
grep 'hy ty' paste_y.txt
[[ ! -f paste_z.txt ]] || exit -1

# Modify nglob results and rerun
rm tail_y.txt
python3 - << EOD
from stepup.core.interact import *
watch_delete("tail_y.txt")
run()
wait()
graph("current_graph_03")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ ! -f paste_ignored.txt ]] || exit -1
grep 'hx tx' paste_x.txt
[[ ! -f paste_y.txt ]] || exit -1
[[ ! -f paste_z.txt ]] || exit -1

# Wait for background processes, if any.
wait

# Modify nglob results and restart
echo "ty" > tail_y.txt
echo "tz" > tail_z.txt
rm -r .stepup/logs
stepup -e -w 1 plan.py & # > current_stdout_b.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_04")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ ! -f paste_ignored.txt ]] || exit -1
grep 'hx tx' paste_x.txt
grep 'hy ty' paste_y.txt
grep 'hz tz' paste_z.txt
