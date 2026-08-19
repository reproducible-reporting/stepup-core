#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
sb -j 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f hello.txt ]] || exit 1
grep overwritten hello.txt

# toch hello.txt and rerun
touch hello.txt
sleep 0.5
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f hello.txt ]] || exit 1
grep overwritten hello.txt

# Call stepup clean to remove all outputs
stepup clean --all --commit > current_cleanup.txt

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f hello.txt ]] || exit 1
grep overwritten hello.txt
