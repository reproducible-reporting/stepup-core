#!/usr/bin/env -S bash -x
source ../example.rc

# Baseline: both variables requested, DFTHYH starts at BBBB.
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

# Change the value of DFTHYH and restart. The new value must be detected, causing a rerun.
export ENV_VAR_TEST_STEPUP_DFTHYH="CCCC"
rm .stepup/*.log
sb -j 1 -w -e & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

[[ -f plan.py ]] || exit 1
grep AAAA current_variables.txt
grep CCCC current_variables.txt
! grep -q BBBB current_variables.txt
cp current_variables.txt current_variables2.txt
