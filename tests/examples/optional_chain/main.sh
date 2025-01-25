#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example without mandatory steps.
cp plan1.py plan.py
stepup -w -e -n 1 plan.py & # > current_stdout1.txt &

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

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f foo1.txt ]] || exit 1
[[ ! -f foo2.txt ]] || exit 1
[[ ! -f bar.txt ]] || exit 1
[[ ! -f egg.txt ]] || exit 1


# Restart the example with a mandatory step.
cp plan2.py plan.py
rm .stepup/director.log
stepup -w -e -n 1 plan.py & # > current_stdout2.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph2")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f foo1.txt ]] || exit 1
[[ -f foo2.txt ]] || exit 1
[[ -f bar.txt ]] || exit 1
[[ -f egg.txt ]] || exit 1
[[ -f spam.txt ]] || exit 1


# Restart the example again without mandatory steps.
cp plan1.py plan.py
rm .stepup/director.log
stepup -w -e -n 1 plan.py & # > current_stdout1.txt &

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

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f foo1.txt ]] || exit 1
[[ ! -f foo2.txt ]] || exit 1
[[ ! -f bar.txt ]] || exit 1
[[ ! -f egg.txt ]] || exit 1
[[ ! -f spam.txt ]] || exit 1
