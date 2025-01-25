#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the plan with the initial input.
echo a > inp1.txt
echo a > inp2.txt
echo a > inp3.txt
export LEVEL=1
stepup -w -n 1 plan.py & # > current_stdout1.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Wait for StepUp to complete
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
[[ -f inp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
[[ -f inp3.txt ]] || exit 1
[[ -f out1.txt ]] || exit 1
[[ -f out2.txt ]] || exit 1
[[ -f out3.txt ]] || exit 1
grep 'level=1' out1.txt
grep 'level=1' out2.txt
grep 'level=1' out3.txt


# Change the input of the last and remove the first.
export LEVEL=2
rm inp1.txt
echo b > inp3.txt
rm .stepup/*.log
stepup -w -n 1 plan.py & # > current_stdout2.txt &
PID=$!

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Wait for StepUp to complete
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph2")
join()
EOD

# Wait for background processes, if any.
# There must be pending steps.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 3 ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f inp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
[[ -f inp3.txt ]] || exit 1
[[ -f out1.txt ]] || exit 1
[[ -f out2.txt ]] || exit 1
[[ -f out3.txt ]] || exit 1
grep 'level=1' out1.txt
grep 'level=1' out2.txt
grep 'level=1' out3.txt


# Restore the initial input of the first step
export LEVEL=3
echo a > inp1.txt
rm .stepup/*.log
stepup -w -n 1 plan.py & # > current_stdout3.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Wait for StepUp to complete
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph3")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
[[ -f inp3.txt ]] || exit 1
[[ -f out1.txt ]] || exit 1
[[ -f out2.txt ]] || exit 1
[[ -f out3.txt ]] || exit 1
grep 'level=1' out1.txt  # Skipped
grep 'level=1' out2.txt  # Not even skipped
grep 'level=3' out3.txt
