#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
echo pass1 > data.txt
stepup -w -n 1 --log-level INFO & # > current_stdout.txt &

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
[[ -f work.py ]] || exit 1
[[ -f optional.txt ]] || exit 1
[[ -f work.out ]] || exit 1
grep pass1 optional.txt

# Modify data.txt and rerun
echo pass2 > data.txt
python3 - << EOD
from stepup.core.interact import *
watch_update("data.txt")
run()
wait()
graph("current_graph2")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f work.py ]] || exit 1
[[ -f optional.txt ]] || exit 1
[[ -f work.out ]] || exit 1
grep pass2 optional.txt
