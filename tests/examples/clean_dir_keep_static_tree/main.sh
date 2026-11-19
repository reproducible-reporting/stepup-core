#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example with plan1, which builds an output in data/.
cp plan1.py plan.py
sb -j 1 -w & # > current_stdout_1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph_1
stepup join

# Wait for background processes, if any.
wait

# StepUp created the directory for the output.
[[ -f data/x.txt ]] || exit 1

# Switch to plan2, which declares data/ as a static tree instead.
cp plan2.py plan.py
sb -j 1 -w & # > current_stdout_2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph_2
stepup join

# Wait for background processes, if any.
wait

# The leftover output became a static file, so it is kept, and so is its directory.
[[ -f data/x.txt ]] || exit 1
