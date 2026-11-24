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
set +e; wait $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

# Check files that are expected to be present.
[[ -f extra.txt ]] || exit 1
[[ -f child/result.txt ]] || exit 1
[[ -f current_all_info.json ]] || exit 1
