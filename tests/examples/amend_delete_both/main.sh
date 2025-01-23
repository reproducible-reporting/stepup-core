#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Create input files
echo "This is the first half and" > asource1.txt
echo "this is the second half." > asource2.txt

# Run the plan.
stepup -w -e -n 1 plan.py & # > current_stdout1.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Initial graph
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
[[ -f asource1.txt ]] || exit 1
[[ -f asource2.txt ]] || exit 1
[[ -f data1.txt ]] || exit 1
[[ -f data2.txt ]] || exit 1
[[ -f log.txt ]] || exit 1

# Remove both intermediates and restart
rm data1.txt data2.txt log.txt asource1.txt
stepup -w -e -n 1 --log-level INFO plan.py & # > current_stdout2.txt &
PID=$!

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Initial graph
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph2")
join()
EOD

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 3 ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f asource1.txt ]] || exit 1
[[ -f asource2.txt ]] || exit 1
[[ ! -f data1.txt ]] || exit 1
[[ -f data2.txt ]] || exit 1
[[ ! -f log.txt ]] || exit 1
