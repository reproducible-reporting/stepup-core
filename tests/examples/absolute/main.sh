#!/usr/bin/env -S bash -x
# Exit on first error and cleanup.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Prepare
export mytmpdir=$(mktemp -d)
trap 'rm -rf $tmpdir' EXIT
PATH_SRC="$mytmpdir/this_is_potentially_unsafe_18731"
PATH_DST="$mytmpdir/this_is_potentially_unsafe_79824"
echo hello > $PATH_SRC
rm -rf $PATH_DST

# Run the example
stepup -w -n 1 plan.py & # > current_stdout.txt &

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
grep hello $PATH_SRC
grep hello $PATH_DST

# Modify absolute path and rerun
echo changed > $PATH_SRC
python3 - << EOD
from stepup.core.interact import *
watch_update("$PATH_SRC")
run()
wait()
graph("current_graph2")
join()
EOD

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
grep changed $PATH_SRC
grep changed $PATH_DST
