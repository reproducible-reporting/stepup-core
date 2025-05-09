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
graph("current_graph")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f helper.py ]] || exit 1
[[ -f work.py ]] || exit 1
[[ -f data-5.0.txt ]] || exit 1
[[ -f data+7.0.txt ]] || exit 1
grep "Standard output for -5.0" stdout-5.0.txt
grep "Standard output for +7.0" stdout+7.0.txt
grep "Standard error for -5.0" stderr-5.0.txt
grep "Standard error for +7.0" stderr+7.0.txt
