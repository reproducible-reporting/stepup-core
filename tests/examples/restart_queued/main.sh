#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the plan.
echo version1 > inp.txt
stepup -w -e -n 1 & # > current_stdout1.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph1")
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp.txt ]] || exit 1
[[ -f out.txt ]] || exit 1
[[ -f final.txt ]] || exit 1
grep version1 out.txt
grep version1 final.txt

# Change  the input and make sure the change is observed, but don't update the output.
echo version2 > inp.txt
python3 - << EOD
from stepup.core.interact import *
watch_update("inp.txt")
graph("current_graph2")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp.txt ]] || exit 1
[[ -f out.txt ]] || exit 1
[[ -f final.txt ]] || exit 1
grep version1 out.txt
grep version1 final.txt

# Restart StepUp after removing the output.
echo version2 > inp.txt
rm .stepup/*.log
stepup -w -e -n 1 & # > current_stdout2.txt &
PID=$!

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph3")
join()
EOD

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp.txt ]] || exit 1
[[ -f out.txt ]] || exit 1
[[ -f final.txt ]] || exit 1
grep version2 out.txt
grep version2 final.txt
