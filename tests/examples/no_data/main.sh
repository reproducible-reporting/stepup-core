#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
cp original.txt data.txt
sb -j 1 -w -e & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f original.txt ]] || exit 1
[[ -f data.txt ]] || exit 1
[[ -f analyzed.txt ]] || exit 1

# Remove the data file and run again
rm data.txt
stepup watch-delete data.txt
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f original.txt ]] || exit 1
[[ -f analyzed.txt ]] || exit 1

# Create file again and restart
cp original.txt data.txt
rm .stepup/*.log
sb -j 1 -w -e & # > current_stdout2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f original.txt ]] || exit 1
[[ -f data.txt ]] || exit 1
[[ -f analyzed.txt ]] || exit 1
