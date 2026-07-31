#!/usr/bin/env -S bash -x
source ../example.rc

# The base directory must exist up front: dir_loop only watches directories that
# already exist, so creating data/ later is a pre-existing, out-of-scope limitation.
mkdir data

# Run the example
sb -j 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
grep "MATCHES: \[\]" .stepup/success.log

# The pattern has zero matches, but its base directory is still watched (via
# glob_base_dir), so creating the first match is noticed.
echo one > data/a.txt
stepup watch-update data/a.txt
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

grep "MATCHES: \['data/a.txt'\]" .stepup/success.log

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f data/a.txt ]] || exit 1
