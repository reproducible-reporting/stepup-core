#!/usr/bin/env -S bash -x
source ../example.rc

# First start, in watch mode.
cp plan1.py plan.py
echo first > input.txt
export ENV_VAR_TEST_STEPUP_PRVDGT=first
sb -j 1 -w & # > current_stdout1.txt &

stepup wait

# Remove plan.py while the director watches it, and stop the director.
rm plan.py
stepup wait -d plan.py
stepup join

# Wait for background processes, if any.
wait

# Restore the same plan.py, change the input and the variable, and start again.
# All three steps must run.
cp plan1.py plan.py
echo second > input.txt
export ENV_VAR_TEST_STEPUP_PRVDGT=second
rm .stepup/*.log
sb -j 1 -w & # > current_stdout2.txt &

stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

grep -qx SECOND upper.txt
grep -qx second variable.txt
grep -qx 2 startup_i.txt
