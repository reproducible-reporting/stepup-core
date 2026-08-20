#!/usr/bin/env -S bash -x
source ../example.rc

# Run the first phase, in which out.txt is built and consumed normally.
cp plan1.py plan.py
sb -j 1 -w & # > current_stdout1.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f out.txt ]] || exit 1
[[ -f used1.txt ]] || exit 1

# Run the second phase, which only changes the command of the consumer of out.txt.
cp plan2.py plan.py
sb -j 1 -w & # > current_stdout2.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

# Check that out.txt kept its producer, so the new consumer could run.
[[ -f out.txt ]] || exit 1
[[ -f used2.txt ]] || exit 1
[[ ! -f used1.txt ]] || exit 1
