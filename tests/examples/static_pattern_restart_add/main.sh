#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
sb -j 1 -w & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f inp1.txt ]] || exit 1
[[ ! -f out1.txt ]] || exit 1

# Create the first file and restart: the plan step must become pending because the
# pattern registered by static() has a new match.
echo two > inp2.txt
sb -j 1 -w -e & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp2.txt ]] || exit 1
[[ -f out2.txt ]] || exit 1
grep two out2.txt
