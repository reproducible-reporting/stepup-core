#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
sb -j 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f src/a.txt ]] || exit 1
grep "FILES: \['src/a.txt'\]" .stepup/success.log

# Add a new file matching the glob(), without consuming it as a step input.
# Before the fix, the static tree's directory was never watched until a match
# was used as a step input, so `stepup wait -u` below would hang forever.
echo bravo > src/b.txt
stepup wait -u src/b.txt
stepup rebuild
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f src/a.txt ]] || exit 1
[[ -f src/b.txt ]] || exit 1
grep "FILES: \['src/a.txt', 'src/b.txt'\]" .stepup/success.log
