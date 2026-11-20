#!/usr/bin/env -S bash -x
source ../example.rc

# The directory remote/ stands in for a remote server, which StepUp does not watch.
mkdir remote
echo first > remote/data.txt

# First start, online: all three steps run.
sb -j 1 -w & # > current_stdout1.txt &
PID=$!

stepup wait
stepup join

set +e; wait $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1
diff report.txt remote/report.txt

# Second start, online, with the remote out of reach, standing in for a lost connection.
# STEPUP_DIRECTOR_I becomes 2, so pull.py runs, and it fails.
mv remote unreachable
rm .stepup/*.log
sb -j 1 -w & # > current_stdout2.txt &
PID=$!

stepup wait
stepup join

set +e; wait $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq $((RETURN_CODE_FAILED | RETURN_CODE_DRAINED)) ]] || exit 1
grep -q 'FAIL.*pull.py' .stepup/fail.log
[[ -f data.txt ]] || exit 1

# Third start, offline, so STEPUP_DIRECTOR_I stays at 2.
# pull.py runs because it failed, and keeps data.txt, so the report is skipped.
# push.py runs because its stored hash was made with 1, and it sends nothing.
rm .stepup/*.log
sb -j 1 -w --offline & # > current_stdout3.txt &
PID=$!

stepup wait
stepup graph current_graph
stepup join

set +e; wait $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1
grep -q 'START.*pull.py' .stepup/success.log
grep -q 'SKIP.*report.txt' .stepup/success.log
grep -q 'START.*push.py' .stepup/success.log
grep -q 'Offline, not sending report.txt.' .stepup/success.log
