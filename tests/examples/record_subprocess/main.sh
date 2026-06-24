#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example.
stepup boot -j 1 -w & # > current_stdout1.txt &
PID=$!

stepup wait
stepup graph current_graph1
stepup join

# The subprocess wrote the step output.
[[ -f out.txt ]] || exit 1
grep -q hello out.txt

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

# With the director shut down, inspect the recorded subprocess invocation.
./checkdb.py
