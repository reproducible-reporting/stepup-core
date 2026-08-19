#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example.
# Four jobs are needed because three steps block on a file created by another step,
# while the driver is running or deferred.
sb -j 4 -w & # > current_stdout.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

# The driver must have been deferred exactly twice.
# Without these defers, it would never recycle its work step.
[[ "$(grep -c DEFERRED .stepup/success.log)" -eq 2 ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f driver.py ]] || exit 1
[[ -f wait_for.py ]] || exit 1
[[ -f gate1.txt ]] || exit 1
[[ -f gate2.txt ]] || exit 1
[[ -f trigger_driver.txt ]] || exit 1
[[ -f trigger_work.txt ]] || exit 1
