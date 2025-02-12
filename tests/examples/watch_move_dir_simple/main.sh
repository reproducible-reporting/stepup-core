#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the initial plan.
mkdir -p data/sub
echo hi > data/sub/inp.txt
stepup -w -n 1 & # > current_stdout.txt &
PID=$!

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
[[ -f data/sub/inp.txt ]] || exit 1
[[ -f data/sub/out.txt ]] || exit 1

# Rename the data directory and rerun
mv data other
python3 - << EOD
from stepup.core.interact import *
watch_delete("data/")
run()
wait()
graph("current_graph2")
EOD

# Move back
mv other data
python3 - << EOD
from stepup.core.interact import *
watch_update("data/")
run()
wait()
graph("current_graph3")
join()
EOD

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f data/sub/inp.txt ]] || exit 1
[[ -f data/sub/out.txt ]] || exit 1
