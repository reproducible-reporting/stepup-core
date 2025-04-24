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
[[ -f ping.txt ]] || exit 1
[[ -f pong.txt ]] || exit 1
[[ -f work.txt ]] || exit 1

# Remove both intermediates and output
rm ping.txt pong.txt work.txt
python3 - << EOD
from stepup.core.interact import *
watch_delete("ping.txt")
watch_delete("pong.txt")
run()
wait()
graph("current_graph2")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f ping.txt ]] || exit 1
[[ -f pong.txt ]] || exit 1
[[ -f work.txt ]] || exit 1
