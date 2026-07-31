#!/usr/bin/env -S bash -x
source ../example.rc

mkdir data
echo one > data/a.txt

# Run the example
sb -j 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
grep "MATCHES: \['data/a.txt'\]" .stepup/success.log

# Delete the whole directory: data/a.txt has no node of its own, so only
# relevant_paths' glob-match extension notices its disappearance via a DELETED_PARENT
# event on data/.
rm -r data
stepup watch-delete data/a.txt
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

grep "MATCHES: \[\]" .stepup/success.log

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -d data ]] || exit 1
