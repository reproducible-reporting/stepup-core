#!/usr/bin/env -S bash -x
source ../example.rc

# Baseline: variables1.json requests only AWDFTD.
export ENV_VAR_TEST_STEPUP_AWDFTD="AAAA"
export ENV_VAR_TEST_STEPUP_DFTHYH="BBBB"
cp variables1.json variables.json
sb -j 1 -w -e & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
[[ -f plan.py ]] || exit 1
grep AAAA current_variables.txt
! grep -q DFTHYH current_variables.txt
cp current_variables.txt current_variables1.txt

# Add a second requested variable by changing the content of variables.json.
# This grows the step's environment set, so it must rerun.
cp variables2.json variables.json
stepup watch-update variables.json
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

[[ -f plan.py ]] || exit 1
grep AAAA current_variables.txt
grep BBBB current_variables.txt
cp current_variables.txt current_variables2.txt
