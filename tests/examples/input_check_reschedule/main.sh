#!/usr/bin/env -S bash -x
source ../example.rc

echo v0 > trigger.txt

sb -j 1 -w --reschedule-cap=1 & # > current_stdout.txt &
PID=$!

stepup wait
stepup graph current_graph
stepup join

# work.py tampers trigger.txt and amends the unavailable never.txt.
# The step must not be rescheduled because of the input change and fail instead.
grep "FAIL │ ./work.py" .stepup/fail.log || exit 1
grep "ERROR │ The scheduler has been put on hold due to unexpected input changes." .stepup/fail.log || exit 1

set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 18 ]] || exit 1

[[ ! -f never.txt ]] || exit 1
