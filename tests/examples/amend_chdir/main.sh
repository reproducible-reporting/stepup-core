#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example.
sb -j 1 -w & # > current_stdout.txt &
PID=$!

# Get graph after normal run.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes and check the exit code.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f sub/copy1.txt ]] || exit 1
[[ -f sub/copy2.txt ]] || exit 1
[[ -f sub/copy3.txt ]] || exit 1
[[ -f sub/copy4.txt ]] || exit 1
[[ ! -f copy1.txt ]] || exit 1
[[ ! -f copy2.txt ]] || exit 1
