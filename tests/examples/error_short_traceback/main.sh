#!/usr/bin/env -S bash -x
source ../example.rc

# The test harness exports STEPUP_DEBUG=1 for every example, which disables the shortening.
# This example is about the default behavior, i.e. without it.
unset STEPUP_DEBUG

# Run the example
sb -j 1 -w & # > current_stdout.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup join

# The traceback itself is not part of current_stdout.txt: the test builder replaces the
# `Standard error` page with `(stripped)`. The fail log keeps it verbatim.
# Only the user's own frame is shown, with a header saying that frames were removed.
grep -q "Shortened traceback" .stepup/fail.log || exit 1
grep -q "stepup.core.exceptions.CyclicError" .stepup/fail.log || exit 1
grep -q 'File "./plan.py"' .stepup/fail.log || exit 1
# StepUp's own frames, the frames that launched the step, and the RPCError that used to
# carry the director-side traceback are all gone.
! grep -q "stepup/core/" .stepup/fail.log || exit 1
! grep -q "frozen runpy" .stepup/fail.log || exit 1
! grep -q "RPCError" .stepup/fail.log || exit 1

# What the client hid is not lost: the director logs it. This must survive the default log
# level (WARNING), which is the whole point of the record, so it is asserted here rather
# than left to the debug counterpart of this example.
grep -q "Exception in RPC call" .stepup/director.log || exit 1
grep -q "stepup.core.exceptions.CyclicError" .stepup/director.log || exit 1

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq $((RETURN_CODE_FAILED | RETURN_CODE_DRAINED)) ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f a.txt ]] || exit 1
[[ ! -f b.txt ]] || exit 1
