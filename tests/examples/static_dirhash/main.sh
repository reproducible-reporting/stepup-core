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
[[ "${RETURNCODE}" -eq 32 ]] || exit 1

# The reported error and the provenance page it carries are checked against
# expected_stdout2.txt: the log files are wiped at the start of the build phase,
# which follows the startup phase that reports this error.

# The hash job is nobody's to await, so its error must not resurface as a stray future.
! grep -q "Future exception was never retrieved" .stepup/director.log

# Check files that are expected to be present and/or missing.
[[ -d data.txt ]] || exit 1
[[ -f copy.txt ]] || exit 1
