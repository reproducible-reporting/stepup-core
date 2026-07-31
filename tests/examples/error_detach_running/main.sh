#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
sb -j 2 -w & # > current_stdout.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq $((RETURN_CODE_FAILED | RETURN_CODE_ONHOLD)) ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f work.py ]] || exit 1
[[ -f trigger_work.txt ]] || exit 1
