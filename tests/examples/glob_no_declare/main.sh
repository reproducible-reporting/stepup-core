#!/usr/bin/env -S bash -x
source ../example.rc

echo hello > inp.txt

# Run the example
sb -j 1 -w & # > current_stdout.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq "${RETURN_CODE_PENDING}" ]] || exit 1

# The AWAITED, undeclared inp.txt is asserted verbatim by expected_stdout.txt's
# "Unavailable inputs" page instead of a separate grep.

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp.txt ]] || exit 1
[[ ! -f out.txt ]] || exit 1
