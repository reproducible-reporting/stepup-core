#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example with an ordinary file as static input.
echo hello > data.txt
sb -j 1 -w & # > current_stdout1.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

# Replace the static input by a directory, which StepUp cannot hash.
rm -f data.txt
mkdir data.txt

# Run again: the startup phase hashes the static input and must survive the failure.
sb -j 1 -w & # > current_stdout2.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
# The scheduler is put on hold because a file could not be hashed: 32
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq "${RETURN_CODE_ONHOLD}" ]] || exit 1

# Check that the error message was logged.
grep "Could not hash data.txt" .stepup/fail.log || exit 1

# The hash job is nobody's to await, so its error must not resurface as a stray future.
! grep -q "Future exception was never retrieved" .stepup/director.log

# Check files that are expected to be present and/or missing.
[[ -d data.txt ]] || exit 1
[[ -f copy.txt ]] || exit 1
