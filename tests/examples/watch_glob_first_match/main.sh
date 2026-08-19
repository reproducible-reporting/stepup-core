#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
sb -j 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
grep "MATCHES: \[\]" .stepup/success.log

# The pattern has zero matches, and its base directory does not even exist yet.
# The watcher remembers it (via glob_base_dir) and installs the watch when it appears,
# so creating the first match is noticed.
mkdir data
echo one > data/a.txt
stepup wait -u data/a.txt
stepup rebuild
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

grep "MATCHES: \['data/a.txt'\]" .stepup/success.log

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f data/a.txt ]] || exit 1
