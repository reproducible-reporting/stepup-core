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
[[ "${RETURNCODE}" -eq $((RETURN_CODE_FAILED | RETURN_CODE_DRAINED)) ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
# The held step must never have run: amend() must raise before it can be released.
[[ ! -f inp1.txt ]] || exit 1

# The fail log must show the new, clear exception, not a "step(s) remained pending" warning.
grep -q "AmendWhileHoldingError" .stepup/fail.log
