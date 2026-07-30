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
[[ -f plan.py ]] || exit 1
[[ -f src/foo.txt ]] || exit 1
[[ -f src/bar.txt ]] || exit 1
[[ -f out_foo.txt ]] || exit 1
[[ -f out_bar.txt ]] || exit 1
[[ -f alt_foo.txt ]] || exit 1
[[ ! -f alt_bar.txt ]] || exit 1
grep foo out_foo.txt
grep bar out_bar.txt
grep foo alt_foo.txt
