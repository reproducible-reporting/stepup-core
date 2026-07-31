#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
sb -j 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present.
[[ -f first.txt ]] || exit 1
[[ -f second.txt ]] || exit 1
[[ -f both.txt ]] || exit 1
grep "content of first.txt" both.txt
grep "content of second.txt" both.txt
