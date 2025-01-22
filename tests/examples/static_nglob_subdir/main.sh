#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
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

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f sub/plan.py ]] || exit 1
[[ -f sub/inp1.txt ]] || exit 1
[[ -f sub/inp2.txt ]] || exit 1
[[ -f sub/out1.txt ]] || exit 1
[[ -f sub/out2.txt ]] || exit 1
grep one sub/out1.txt
grep two sub/out2.txt

# Wait for background processes, if any.
wait

# Restart with one more input
echo three > sub/inp3.txt
rm .stepup/*.log
stepup -w -e -n 1 plan.py & # > current_stdout2.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Restart StepUp.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph2")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f sub/plan.py ]] || exit 1
[[ -f sub/inp1.txt ]] || exit 1
[[ -f sub/inp2.txt ]] || exit 1
[[ -f sub/inp3.txt ]] || exit 1
[[ -f sub/out1.txt ]] || exit 1
[[ -f sub/out2.txt ]] || exit 1
[[ -f sub/out3.txt ]] || exit 1
grep one sub/out1.txt
grep two sub/out2.txt
grep three sub/out3.txt

# Wait for background processes, if any.
wait
