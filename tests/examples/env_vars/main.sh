#!/usr/bin/env -S bash -x
source ../example.rc

# Prepare some variables. Only AWDFTD is requested by variables1.json (DFTHYH is not),
# so the step must record AWDFTD and ignore DFTHYH.
export ENV_VAR_TEST_STEPUP_AWDFTD="AAAA"
export ENV_VAR_TEST_STEPUP_DFTHYH="BBBB"

# Run the example
cp variables1.json variables.json
sb -j 1 -w -e & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
grep AAAA current_variables.txt
# DFTHYH is set in the shell but not requested, so it must not be recorded.
! grep -q DFTHYH current_variables.txt
cp current_variables.txt current_variables1.txt
