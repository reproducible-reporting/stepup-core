#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
stepup -w -n 1 & # > current_stdout.txt &

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
[[ -f hello.txt ]] || exit 1
grep overwritten hello.txt

# toch hello.txt and rerun
touch hello.txt
sleep 0.5
python3 - << EOD
from stepup.core.interact import *
run()
wait()
graph("current_graph2")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f hello.txt ]] || exit 1
grep overwritten hello.txt

# Call cleanup to remove all outputs
cleanup . > current_cleanup.txt

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f hello.txt ]] || exit 1
grep overwritten hello.txt
