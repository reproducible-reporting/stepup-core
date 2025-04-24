#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example with two input files.
echo one > inp1.txt
echo two > inp2.txt
stepup -w -n 1 & # > current_stdout1.txt &

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
[[ -f inp1.txt ]] || exit 1
[[ -f out1.txt ]] || exit 1
[[ -f foo1.txt ]] || exit 1
grep one foo1.txt
[[ -f inp2.txt ]] || exit 1
[[ -f out2.txt ]] || exit 1
[[ -f foo2.txt ]] || exit 1
grep two foo2.txt

# Remove an input file and restart.
rm inp1.txt
stepup -w -e -n 1 & # > current_stdout2.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

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
[[ ! -f inp1.txt ]] || exit 1
[[ ! -f out1.txt ]] || exit 1
[[ ! -f foo1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
[[ -f out2.txt ]] || exit 1
[[ -f foo2.txt ]] || exit 1
grep two foo2.txt

# Remove also the other input file and restart
rm inp2.txt
stepup -w -e -n 1 & # > current_stdout3.txt &

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
wait

# Check files that are expected to be present and/or missing.
[[ ! -f inp1.txt ]] || exit 1
[[ ! -f out1.txt ]] || exit 1
[[ ! -f foo1.txt ]] || exit 1
[[ ! -f inp2.txt ]] || exit 1
[[ ! -f foo2.txt ]] || exit 1
