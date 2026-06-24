#!/usr/bin/env -S bash -x
source ../example.rc

# Prepare some variables
export ENV_VAR_TEST_STEPUP_SDASFD="AAAA"

# Run the example
sb -j 1 -w -e & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
grep AAAA output.txt

# Rerstart with changed variable
export ENV_VAR_TEST_STEPUP_SDASFD="BBBB"
rm .stepup/*.log
sb -j 1 -w -e & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
grep BBBB output.txt
