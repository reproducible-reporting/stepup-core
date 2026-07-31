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

# All three patterns matched the same static files, without any of them raising.
grep "MAIN ALL: \['data/a.txt', 'data/b.txt'\]" .stepup/success.log
grep "MAIN A: \['data/a.txt'\]" .stepup/success.log
grep "SUB: \['../data/a.txt', '../data/b.txt'\]" .stepup/success.log

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f sub/plan.py ]] || exit 1
[[ -f data/a.txt ]] || exit 1
[[ -f data/b.txt ]] || exit 1
