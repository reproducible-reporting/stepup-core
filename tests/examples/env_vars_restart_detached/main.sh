#!/usr/bin/env -S bash -x
source ../example.rc

# Build with the initial value.
export ENV_VAR_TEST_STEPUP_KQZRPX="AAAA"
cp plan1.py plan.py
sb -j 1 -w -e & # > current_stdout1.txt &
PID=$!

stepup wait
stepup graph current_graph1
stepup join

set +e; wait $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1
grep AAAA out.txt

# Break the plan, so the step is detached when StepUp stops.
rm .stepup/*.log
cp plan2.py plan.py
sb -j 1 -w -e & # > current_stdout2.txt &
PID=$!

stepup wait
stepup graph current_graph2
stepup join

set +e; wait $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq $((RETURN_CODE_FAILED | RETURN_CODE_DRAINED)) ]] || exit 1
grep AAAA out.txt

# Restore the plan and change the value.
# The detached step must be marked pending at startup,
# so it reruns when the plan declares it again.
export ENV_VAR_TEST_STEPUP_KQZRPX="BBBB"
rm .stepup/*.log
cp plan1.py plan.py
sb -j 1 -w -e & # > current_stdout3.txt &
PID=$!

stepup wait
stepup graph current_graph3
stepup join

set +e; wait $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1
grep BBBB out.txt
