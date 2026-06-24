#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example. The plan step is expected to fail.
sb -j 1 -w & # > current_stdout.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e

# result.txt must not exist because the plan step failed before registering any steps.
[[ ! -f result.txt ]] || exit 1
