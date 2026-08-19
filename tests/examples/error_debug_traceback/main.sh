#!/usr/bin/env -S bash -x
source ../example.rc

# No `unset STEPUP_DEBUG` here: the test harness exports STEPUP_DEBUG=1 for every example,
# which is exactly the setting this example is about.

# Run the example
sb -j 1 -w & # > current_stdout.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup join

# The traceback itself is not part of current_stdout.txt: the test builder replaces the
# `Standard error` page with `(stripped)`. The fail log keeps it verbatim.
# With STEPUP_DEBUG=1, nothing is left out: the director-side traceback travels to the
# client inside an RPCError, and the client's own traceback keeps every frame.
grep -q "An exception was raised in the server during the call" .stepup/fail.log || exit 1
grep -q "stepup.core.exceptions.CyclicError" .stepup/fail.log || exit 1
grep -q "stepup/core/rpc.py" .stepup/fail.log || exit 1
grep -q "frozen runpy" .stepup/fail.log || exit 1
! grep -q "Shortened traceback" .stepup/fail.log || exit 1

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
# With STEPUP_DEBUG=1, `stepup build` scans its own director log and adds RETURN_CODE_INTERNAL
# for every finding. The absence of that bit shows that the reported usage error, which the
# director logs before hiding it from the client, is not mistaken for an internal problem.
[[ "${RETURNCODE}" -eq $((RETURN_CODE_FAILED | RETURN_CODE_ONHOLD)) ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f a.txt ]] || exit 1
[[ ! -f b.txt ]] || exit 1
