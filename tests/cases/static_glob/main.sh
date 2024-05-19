#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Prepare static inputs
echo "First input" > inp1.txt
echo "Second input" > inp2.txt

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
grep First inp1.txt
grep Second inp2.txt
grep First out1.txt
grep Second out2.txt
[[ ! -f out3.txt ]] || exit -1

# Modify nglob results and rerun
echo "Third input" > inp3.txt
rm inp1.txt
python3 - << EOD
from stepup.core.interact import *
watch_update("inp3.txt")
watch_delete("inp1.txt")
run()
wait()
graph("current_graph_02")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ ! -f out1.txt ]] || exit -1
grep Second inp2.txt
grep Third inp3.txt
grep Second out2.txt
grep Third out3.txt

# Wait for background processes, if any.
wait

# Modify nglob results and restart
echo "Fourth input" > inp4.txt
rm inp2.txt
rm -r .stepup/logs
stepup -e -w 1 plan.py & # > current_stdout_b.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_03")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ ! -f out1.txt ]] || exit -1
[[ ! -f out2.txt ]] || exit -1
grep Third inp3.txt
grep Fourth inp4.txt
grep Third out3.txt
grep Fourth out4.txt

# Wait for background processes, if any.
wait
