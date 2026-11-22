#!/usr/bin/env -S bash -x
source ../example.rc

# First build phase, offline on a new database: the step runs with STEPUP_STARTUP_I=1.
sb -j 1 -w --offline & # > current_stdout.txt &
PID=$!

stepup wait
grep -qx 1 startup_i.txt

# A recheck while offline keeps the value, so the step does not run again.
stepup rebuild --recheck
stepup wait
! grep -qw START .stepup/success.log || exit 1
grep -qx 1 startup_i.txt

stepup graph current_graph
stepup join

set +e; wait $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1
