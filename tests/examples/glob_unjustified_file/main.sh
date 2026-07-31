#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
sb -j 1 -w & # > current_stdout.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
# A warning sets the WARNING bit but never the FAILED bit:
# this is the pinned statement of that rule.
[[ "${RETURNCODE}" -eq "${RETURN_CODE_WARNING}" ]] || exit 1

grep -F "data/a.txt" .stepup/warning.log

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f data/a.txt ]] || exit 1
