#!/usr/bin/env -S bash -x
source ../example.rc

# To trigger the (by now fixed) bug, create the expected outout.
touch rendered.tex
# Run the example
sb -w -j 1 & # > current_stdout.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 2 ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f template.tex ]] || exit 1
[[ -f variables.py ]] || exit 1
[[ -f rendered.tex ]] || exit 1
