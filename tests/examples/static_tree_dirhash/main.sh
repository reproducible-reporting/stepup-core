#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
mkdir -p foo/bar
sb -w & # > current_stdout1.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
# The build fails and the director is on hold: 2 + 32 = 34
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq $((RETURN_CODE_FAILED | RETURN_CODE_ONHOLD)) ]] || exit 1

# The error must point at the offending call in plan.py.
grep -q "Directories are not allowed: foo/bar" .stepup/fail.log
grep -q 'run("echo foo/bar", inp="foo/bar")' .stepup/fail.log

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -d foo ]] || exit 1
[[ -d foo/bar ]] || exit 1

# Run the example again to ensure that the startup phase does not try to hash
# the directory either: the rejected input never entered the workflow.
sb -w & # > current_stdout2.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
# The build fails and the director is on hold: 2 + 32 = 34
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq $((RETURN_CODE_FAILED | RETURN_CODE_ONHOLD)) ]] || exit 1

# The error must point at the offending call in plan.py.
grep -q "Directories are not allowed: foo/bar" .stepup/fail.log
grep -q 'run("echo foo/bar", inp="foo/bar")' .stepup/fail.log

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -d foo ]] || exit 1
[[ -d foo/bar ]] || exit 1
