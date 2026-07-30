#!/usr/bin/env -S bash -x
source ../example.rc

# Phase 1: the subplan consumes ../hop2.txt, so the optional step becomes needed.
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
[[ -f hop2.txt ]] || exit 1
[[ -f sub/hop3.txt ]] || exit 1
grep hello sub/hop4.txt


# Phase 2: the command of the optional step changes, so a fresh step node replaces
# the old one. The subplan is unchanged and is therefore skipped, meaning that none
# of its dependencies are redeclared. The new optional step must still be recognized
# as needed and rerun, after which the whole chain in the subplan is updated.
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

# Check files that are expected to be present and/or missing.
[[ -f hop2.txt ]] || exit 1
[[ -f sub/hop3.txt ]] || exit 1
grep HELLO sub/hop4.txt
