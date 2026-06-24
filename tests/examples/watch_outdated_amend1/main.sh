#!/usr/bin/env -S bash -x
source ../example.rc

# Run with the initial subs.txt.
mkdir -p data
echo inp1 > data/inp1.txt
echo data/inp1.txt > subs.txt
sb -j 1 -w -e & # > current_stdout.txt &

# Initial graph
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f data/inp1.txt ]] || exit 1
[[ -f copy.txt ]] || exit 1
grep inp1 copy.txt

# Change subs.txt and rerun.
rm data/inp1.txt
echo inp2 > data/inp2.txt
echo data/inp2.txt > subs.txt
stepup watch-update subs.txt
stepup watch-delete data/inp1.txt
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f data/inp1.txt ]] || exit 1
[[ -f data/inp2.txt ]] || exit 1
[[ -f copy.txt ]] || exit 1
grep inp2 copy.txt
