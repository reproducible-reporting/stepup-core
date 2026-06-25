#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example.
sb -j 1 -w & # > current_stdout1.txt &
PID=$!

stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

# With the director shut down, inspect the recorded subprocess invocation.
./checkdb.py
