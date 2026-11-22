#!/usr/bin/env -S bash -x
source ../example.rc

# First build phase: both steps run.
echo first > input.txt
sb -j 1 -w & # > current_stdout.txt &

stepup wait
grep -qx 1 startup_i.txt
grep -qx FIRST upper.txt

# Second build phase, after a change of the input of the other step.
# Only that step runs.
echo second > input.txt
stepup wait -u input.txt
stepup rebuild
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# The log holds the last build phase only.
grep -q 'START.*upper.txt' .stepup/success.log
! grep -q 'START.*startup_i.txt' .stepup/success.log || exit 1
grep -qx 1 startup_i.txt
grep -qx SECOND upper.txt
