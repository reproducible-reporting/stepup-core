#!/usr/bin/env -S bash -x
source ../example.rc

# First build phase: all steps run.
echo first > input.txt
sb -j 1 -w & # > current_stdout.txt &
PID=$!

stepup wait
grep -qx 1 shell_i.txt
grep -qx 1 python_i.txt
grep -qx FIRST upper.txt

# Second build phase, after a change of the input of the step without recheck=True.
# A plain rebuild runs only that step.
echo second > input.txt
stepup wait -u input.txt
stepup rebuild
stepup wait

# The log holds the last build phase only.
grep -q 'START.*upper.txt' .stepup/success.log
! grep -q 'START.*shell_i.txt' .stepup/success.log || exit 1
! grep -q 'START.*getstartup.py' .stepup/success.log || exit 1
grep -qx 1 shell_i.txt
grep -qx 1 python_i.txt
grep -qx SECOND upper.txt

# Third build phase, with a recheck and no file changes.
# Only the steps with recheck=True run, and they see the new value.
stepup rebuild --recheck
stepup wait
grep -q 'START.*shell_i.txt' .stepup/success.log
grep -q 'START.*getstartup.py' .stepup/success.log
! grep -q 'START.*upper.txt' .stepup/success.log || exit 1
grep -qx 2 shell_i.txt
grep -qx 2 python_i.txt

stepup graph current_graph
stepup join

set +e; wait $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1
