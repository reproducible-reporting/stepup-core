#!/usr/bin/env -S bash -x
source ../example.rc

# Run with the default behavior: the scheduler is put on hold after "false" fails,
# so the independent "touch" step (alphabetically after "false", hence dispatched
# second with a single job slot) never gets a chance to run.
sb -j 1 -w & # > current_stdout1.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 34 ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f independent.txt ]] || exit 1

# Run again with --keep-going: the independent step now runs to completion
# despite the earlier failure.
sb -j 1 -w -k & # > current_stdout2.txt &
PID=$!

stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 2 ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f independent.txt ]] || exit 1
