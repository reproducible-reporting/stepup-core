#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the plan for the first time.
mkdir -p static
echo first > static/foo.txt
stepup -e -w 1 plan.py & # > current_stdout_01.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Run StepUp for a first time.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_01")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f static/foo.txt ]] || exit -1
[[ -f bar.txt ]] || exit -1
grep first static/foo.txt
grep first bar.txt

# Run the plan again without any changes.
rm -r .stepup/logs
stepup -e -w 1 plan.py & # > current_stdout_02.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Wait for StepUp to complete.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_02")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f static/foo.txt ]] || exit -1
[[ -f bar.txt ]] || exit -1
grep first static/foo.txt
grep first bar.txt

# Run the plan again with changes.
echo second > static/foo.txt
rm -r .stepup/logs
stepup -e -w 1 plan.py & # > current_stdout_03.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Wait for StepUp to complete.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph_03")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit -1
[[ -f static/foo.txt ]] || exit -1
[[ -f bar.txt ]] || exit -1
grep second static/foo.txt
grep second bar.txt
