#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
cp original.txt data.txt
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
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f original.txt ]] || exit 1
[[ -f data.txt ]] || exit 1
[[ -f analyzed.txt ]] || exit 1

# Remove the data file and run again
rm data.txt
python3 - << EOD
from stepup.core.interact import *
watch_delete("data.txt")
run()
wait()
graph("current_graph2")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f original.txt ]] || exit 1
[[ -f analyzed.txt ]] || exit 1

# Wait for background processes, if any.
wait

# Create file again and restart
cp original.txt data.txt
rm .stepup/*.log
stepup -w -e -n 1 plan.py & # > current_stdout2.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph3")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f original.txt ]] || exit 1
[[ -f data.txt ]] || exit 1
[[ -f analyzed.txt ]] || exit 1

# Wait for background processes, if any.
wait
