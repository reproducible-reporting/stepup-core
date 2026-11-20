#!/usr/bin/env -S bash -x
source ../example.rc

# The directory remote/ stands in for a remote server, which StepUp does not watch.
mkdir remote
echo first > remote/data.txt
cp template1.txt template.txt

# First start, online: all three steps run, and the report is uploaded.
sb -j 1 -w & # > current_stdout1.txt &
PID=$!

stepup wait
stepup graph current_graph
stepup join

set +e; wait $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1
diff report.txt remote/report.txt

# Second start, offline, with the remote out of reach, so that any access to it fails.
# STEPUP_DIRECTOR_I stays at 1, so no step starts.
mv remote unreachable
rm .stepup/*.log
sb -j 1 -w --offline & # > current_stdout2.txt &
PID=$!

stepup wait
stepup join

set +e; wait $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1
! grep -qw START .stepup/success.log || exit 1

# Third start, offline, after a comment in the template was changed.
# The report is built again with the same content,
# so push.py is checked and skipped, because its stored hash was made with the same value.
cp template2.txt template.txt
rm .stepup/*.log
sb -j 1 -w --offline & # > current_stdout3.txt &
PID=$!

stepup wait
stepup join

set +e; wait $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1
grep -q 'START.*report.txt' .stepup/success.log
grep -q 'SKIP.*push.py' .stepup/success.log
! grep -q 'START.*pull.py' .stepup/success.log || exit 1
! grep -q 'START.*push.py' .stepup/success.log || exit 1

# Fourth start, offline, after another line of the template was changed.
# The report changes, so push.py runs, and it sends nothing because it is offline.
cp template3.txt template.txt
rm .stepup/*.log
sb -j 1 -w --offline & # > current_stdout4.txt &
PID=$!

stepup wait
stepup join

set +e; wait $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1
grep -q 'START.*push.py' .stepup/success.log
! grep -q 'START.*pull.py' .stepup/success.log || exit 1
grep -qx 'Remote data:' report.txt
grep -qx 'Data from the remote:' unreachable/report.txt

# Fifth start, online, with the remote back.
# Both pull.py and push.py run, the first leaves data.txt alone, so the report is skipped,
# and the second uploads the report of the fourth start.
mv unreachable remote
rm .stepup/*.log
sb -j 1 -w & # > current_stdout5.txt &
PID=$!

stepup wait
stepup join

set +e; wait $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1
grep -q 'START.*pull.py' .stepup/success.log
grep -q 'SKIP.*report.txt' .stepup/success.log
grep -q 'START.*push.py' .stepup/success.log
diff report.txt remote/report.txt
