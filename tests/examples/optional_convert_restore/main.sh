#!/usr/bin/env -S bash -x
source ../example.rc

# Baseline: IDX=3 realises only converted3.
export ENV_VAR_TEST_STEPUP_IDX="3"
sb -j 1 -w -e & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
stepup join
wait

[[ -f converted3.txt ]] || exit 1
grep raw3 used.txt

# Restart with IDX=1: converted3 is dropped and converted1 is realised.
export ENV_VAR_TEST_STEPUP_IDX="1"
rm .stepup/*.log
sb -j 1 -w -e & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join
wait

[[ -f converted1.txt ]] || exit 1
grep raw1 used.txt
[[ ! -f converted3.txt ]] || exit 1

# Restart back to IDX=3: the previously-dropped converted3 is rebuilt.
export ENV_VAR_TEST_STEPUP_IDX="3"
rm .stepup/*.log
sb -j 1 -w -e & # > current_stdout3.txt &

stepup wait
stepup graph current_graph3
stepup join
wait

[[ -f plan.py ]] || exit 1
[[ -f converted3.txt ]] || exit 1
[[ -f used.txt ]] || exit 1
grep raw3 used.txt
[[ ! -f converted1.txt ]] || exit 1
[[ ! -f converted2.txt ]] || exit 1
[[ ! -f converted4.txt ]] || exit 1
