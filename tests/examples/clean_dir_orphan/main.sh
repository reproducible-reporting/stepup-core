#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example with plan1.
cp plan1.py plan.py
sb -j 1 -w & # > current_stdout_1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph_1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f dir1/hello.txt ]] || exit 1
[[ -f dir2/world.txt ]] || exit 1

# Remove the output by hand, but keep its directory, and switch to plan2.
rm dir2/world.txt
cp plan2.py plan.py
sb -j 1 -w & # > current_stdout_2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph_2
stepup join

# Wait for background processes, if any.
wait

# The directory left behind by the manual removal is cleaned up.
[[ -f plan.py ]] || exit 1
[[ -f dir1/hello.txt ]] || exit 1
[[ ! -d dir2 ]] || exit 1
