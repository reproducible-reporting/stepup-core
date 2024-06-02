#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
xargs rm -rvf < .gitignore

# Run the example
mkdir sub/
echo hello > sub/message.txt
stepup -w 1 plan.py & # > current_stdout.txt &

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
[[ -f plan.py ]] || exit 1
[[ -f sub/message.txt ]] || exit 1
[[ -f message.txt ]] || exit 1

# Create the required static files and try again
rm sub/message.txt
sleep 0.1
rmdir sub/
sleep 0.1
rm message.txt
python3 - << EOD
from stepup.core.interact import *
watch_delete("sub/")
run()
wait()
graph("current_graph_02")
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f sub/message.txt ]] || exit 1
[[ ! -f message.txt ]] || exit 1

# Create again and rerun
mkdir sub/
echo hello > sub/message.txt

python3 - << EOD
from stepup.core.interact import *
watch_update("sub/message.txt")
run()
wait()
graph("current_graph_03")
join()
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f sub/message.txt ]] || exit 1
[[ -f message.txt ]] || exit 1

# Wait for background processes, if any.
wait
