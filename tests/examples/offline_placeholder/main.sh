#!/usr/bin/env -S bash -x
source ../example.rc

# The directory external/ stands in for a remote server, which StepUp does not watch.
# It does not exist yet, so that any access to it fails.

# First start, offline: cite.py writes a placeholder.
sb -j 1 -w --offline & # > current_stdout1.txt &
PID=$!

stepup wait
stepup graph current_graph
stepup join

set +e; wait $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1
grep -qx '\[smith2024: citation not available offline\]' report.txt

# Second start, offline: STEPUP_BUILD_OFFLINE keeps its value, so no step starts.
rm .stepup/*.log
sb -j 1 -w --offline & # > current_stdout2.txt &
PID=$!

stepup wait
stepup join

set +e; wait $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1
! grep -qw START .stepup/success.log || exit 1

# Third start, online, with the external data within reach.
# STEPUP_BUILD_OFFLINE changes, so cite.py runs and replaces the placeholder.
mkdir external
echo "Smith, J. (2024). A study of placeholders." > external/smith2024.txt
rm .stepup/*.log
sb -j 1 -w & # > current_stdout3.txt &
PID=$!

stepup wait
stepup join

set +e; wait $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1
grep -q 'START.*cite.py' .stepup/success.log
grep -qx 'Smith, J. (2024). A study of placeholders.' report.txt
