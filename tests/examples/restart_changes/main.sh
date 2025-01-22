#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the first plan.
stepup -w -e -n 1 plan1.py & # > current_stdout1.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Run StepUp for a first time.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph1")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan1.py ]] || exit 1
[[ -f copy1.txt ]] || exit 1
[[ -f copy_both1.txt ]] || exit 1
[[ -f source_both.txt ]] || exit 1
[[ -f source1.txt ]] || exit 1

# second with a different plan.
rm .stepup/*.log
stepup -w -e -n 1 plan2.py & # > current_stdout2.txt &

# Wait for the director and get its socket.
export STEPUP_DIRECTOR_SOCKET=$(
  python -c "import stepup.core.director; print(stepup.core.director.get_socket())"
)

# Restart StepUp.
python3 - << EOD
from stepup.core.interact import *
wait()
graph("current_graph2")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan1.py ]] || exit 1
[[ -f plan2.py ]] || exit 1

# Static files should never be removed.
[[ -f source_both.txt ]] || exit 1
[[ -f source1.txt ]] || exit 1
[[ -f source2.txt ]] || exit 1

# Only the output file of the second should remain.
[[ ! -f copy1.txt ]] || exit 1
[[ ! -f copy_both1.txt ]] || exit 1
[[ -f copy2.txt ]] || exit 1
[[ -f copy_both2.txt ]] || exit 1
