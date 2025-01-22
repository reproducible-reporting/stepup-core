#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
stepup -w -n 1 'poor plan.py' & # > current_stdout.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Get the graph after completion of the pending steps.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f 'poor plan.py' ]] || exit 1
[[ -f 'call is cool.py' ]] || exit 1
[[ -f 'script is cool.py' ]] || exit 1
[[ -f 'call is cool_out.json' ]] || exit 1
[[ -f 'the road to hell is paved with whitespace.md' ]] || exit 1
[[ -f 'white space leaves a lot of room for mistakes.md' ]] || exit 1
