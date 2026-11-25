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

# Check files that are expected to be present and/or missing.
[[ -f sub/data/a.out ]] || exit 1
[[ -f sub/vol.txt ]] || exit 1
[[ -f current_tracked_info.json ]] || exit 1
