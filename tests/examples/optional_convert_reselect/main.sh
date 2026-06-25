#!/usr/bin/env -S bash -x
source ../example.rc

# Baseline: IDX=3 realises only converted3.
export ENV_VAR_TEST_STEPUP_IDX="3"
sb -j 1 -w -e & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

[[ -f converted3.txt ]] || exit 1
grep raw3 used.txt
[[ ! -f converted1.txt ]] || exit 1

# Restart with a different selection. converted1 is realised and converted3 is dropped.
export ENV_VAR_TEST_STEPUP_IDX="1"
rm .stepup/*.log
sb -j 1 -w -e & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

[[ -f plan.py ]] || exit 1
[[ -f converted1.txt ]] || exit 1
[[ -f used.txt ]] || exit 1
grep raw1 used.txt
[[ ! -f converted2.txt ]] || exit 1
[[ ! -f converted3.txt ]] || exit 1
[[ ! -f converted4.txt ]] || exit 1
