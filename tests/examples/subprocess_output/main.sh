#!/usr/bin/env -S bash -x
source ../example.rc

# First build.
cp plan1.py plan.py
sb -j 1 -w & # > current_stdout1.txt &
PID=$!

stepup wait

# Check that no failures occurred.
[[ ! -f .stepup/fail.log ]] || exit 1

# Assert that both the Python-level and the subprocess output were captured.
# The success log keeps the full, un-stripped standard output and standard error,
# so it can be grepped for the subprocess lines (the behaviour under test).
grep -F "python-stdout-line" .stepup/success.log
grep -F "subprocess-stdout-line" .stepup/success.log
grep -F "subprocess-stderr-line" .stepup/success.log

# Force a skip of ./work.py while keeping its output: change plan.py so it re-runs,
# but leave the work.py step declaration and its input unchanged. The skip path
# (try_skip_job) does not call clean_before_run, so the stored output must survive.
> .stepup/success.log
cp plan2.py plan.py
stepup watch-update plan.py
stepup run
stepup wait
stepup graph current_graph1
grep "SKIP │ ./work.py" .stepup/success.log

stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

# With the director shut down, assert the captured output is persisted in step_output.
# The stderr page in expected_stdout is stripped before comparison, so the persisted
# stderr is asserted here via the database instead. The output stored during the first
# real execution must still be present after the skip.
./checkdb.py

# Restart StepUp from scratch and confirm the step_output rows survive a full restart.
rm .stepup/*.log
sb -j 1 -w & # > current_stdout2.txt &
PID=$!

stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

# Assert the captured output is persisted in step_output after a full restart.
./checkdb.py
