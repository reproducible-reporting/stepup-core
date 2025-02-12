#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the plan.
stepup -w -n 1 plan.py & # > current_stdout.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Run StepUp for a first time.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph1")
EOD

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f line.txt ]] || exit 1
[[ -f log.txt ]] || exit 1
[[ $(wc -l log.txt | cut -d' ' -f 1) -eq 1 ]] || exit 1

# Restart StepUp.
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
[[ -f line.txt ]] || exit 1
[[ -f log.txt ]] || exit 1
[[ $(wc -l log.txt | cut -d' ' -f 1) -eq 2 ]] || exit 1
