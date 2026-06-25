#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example. ENV_VAR_TEST_STEPUP_IDX selects which optional conversion is needed.
export ENV_VAR_TEST_STEPUP_IDX="3"
sb -j 1 -w -e & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1

# Rerun without changes; the result must be idempotent.
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Only the needed optional conversion (converted3) is realised.
[[ -f plan.py ]] || exit 1
[[ -f converted3.txt ]] || exit 1
[[ -f used.txt ]] || exit 1
grep raw3 used.txt
[[ ! -f converted1.txt ]] || exit 1
[[ ! -f converted2.txt ]] || exit 1
[[ ! -f converted4.txt ]] || exit 1
