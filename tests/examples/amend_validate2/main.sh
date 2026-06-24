#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
echo lingering > pong.txt
sb -j 1 -w & # > current_stdout.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f ping.txt ]] || exit 1
[[ -f pong.txt ]] || exit 1

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 4 ]] || exit 1
