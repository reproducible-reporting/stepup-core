#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example, version 1
cp plan1.py plan.py
stepup boot -j 1 -w -e & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f output.txt ]] || exit 1

# Run the example, version 2
cp plan2.py plan.py
rm .stepup/*.log
stepup boot -j 1 -w -e & # > current_stdout2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f output.txt ]] || exit 1
