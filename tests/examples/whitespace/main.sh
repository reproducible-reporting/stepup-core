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
[[ -f 'plan.py' ]] || exit 1
[[ -f 'script is cool.py' ]] || exit 1
[[ -f 'the road to hell is paved with whitespace.txt' ]] || exit 1
[[ -f 'white space leaves a lot of room for mistakes.txt' ]] || exit 1
