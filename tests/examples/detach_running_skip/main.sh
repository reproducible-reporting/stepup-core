#!/usr/bin/env -S bash -x
source ../example.rc

# First run: plan.py fails while work.py is still running, detaching it.
cp plan1.py plan.py
sb -j 2 -w & # > current_stdout1.txt &
PID=$!

stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq $((RETURN_CODE_FAILED | RETURN_CODE_ONHOLD)) ]] || exit 1

# The detached step ran to completion and recorded its output.
[[ -f trigger_work.txt ]] || exit 1
[[ "$(cat out.txt)" == "1" ]] || exit 1

# Second run: the plan no longer fails and recreates work.py identically.
rm trigger_work.txt
cp plan2.py plan.py
sb -j 2 -w & # > current_stdout2.txt &
PID=$!

stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

# work.py was skipped: it neither rewrote the trigger nor bumped the counter.
[[ ! -f trigger_work.txt ]] || exit 1
[[ "$(cat out.txt)" == "1" ]] || exit 1
