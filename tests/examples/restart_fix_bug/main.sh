#!/usr/bin/env -S bash -x
source ../example.rc

# Run the first plan.
cp plan1.py plan.py
sb -j 1 -w -e & # > current_stdout1.txt &
PID=$!

# Run StepUp for a first time.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 2 ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f out.txt ]] || exit 1

# second with a different plan.
rm .stepup/*.log
cp plan2.py plan.py
sb -j 1 -w -e & # > current_stdout2.txt &
PID=$!

# Restart StepUp.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f out.txt ]] || exit 1
