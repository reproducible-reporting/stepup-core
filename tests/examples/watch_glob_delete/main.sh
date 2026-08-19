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

# data/a.txt is a glob match with no node of its own: is_relevant must still catch its
# deletion so ./plan.py reruns, without removing the whole directory (that is
# watch_glob_delete_dir's scenario).
rm data/a.txt
stepup wait -d data/a.txt
stepup rebuild
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

grep "MATCHES: \[\]" .stepup/success.log

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -d data ]] || exit 1
[[ ! -f data/a.txt ]] || exit 1
