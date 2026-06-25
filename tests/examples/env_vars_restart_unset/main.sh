#!/usr/bin/env -S bash -x
source ../example.rc

# Baseline: both variables requested and set.
export ENV_VAR_TEST_STEPUP_AWDFTD="AAAA"
export ENV_VAR_TEST_STEPUP_DFTHYH="BBBB"
cp variables2.json variables.json
sb -j 1 -w -e & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

[[ -f plan.py ]] || exit 1
grep AAAA current_variables.txt
grep BBBB current_variables.txt
cp current_variables.txt current_variables1.txt

# Unset a requested variable and restart. Its absence must be detected, causing a rerun.
unset ENV_VAR_TEST_STEPUP_AWDFTD
rm .stepup/*.log
sb -j 1 -w -e & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

[[ -f plan.py ]] || exit 1
! grep -q AAAA current_variables.txt
grep BBBB current_variables.txt
cp current_variables.txt current_variables2.txt
