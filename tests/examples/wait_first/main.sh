#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
echo hello > inp.txt
stepup boot -j 1 -W & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp.txt ]] || exit 1
[[ -f out.txt ]] || exit 1
grep hello out.txt

# Change the input, which should start the builder automatically.
echo bye > inp.txt

# Get the graph after completion of the pending steps.
sleep 1
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp.txt ]] || exit 1
[[ -f out.txt ]] || exit 1
grep bye out.txt
