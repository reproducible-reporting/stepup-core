#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example.
sb -j 1 -w & # > current_stdout.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

# Check files that are expected to be present.
[[ -f stable.txt ]] || exit 1
[[ -f volatile.txt ]] || exit 1
grep stable stable.txt
