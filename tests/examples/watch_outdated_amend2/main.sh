#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run with the initial subs.txt.
echo inp1 > inp1.txt
echo conv1.txt > subs1.txt
stepup boot -n 1 -w -e & # > current_stdout.txt &

# Initial graph
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp1.txt ]] || exit 1
[[ -f subs1.txt ]] || exit 1
[[ -f subs2.txt ]] || exit 1
grep conv1.txt subs2.txt
[[ -f subs3.txt ]] || exit 1
grep conv1.txt subs3.txt

# Remove inp1.txt, change inp2.txt and subs1.txt and rerun.
rm inp1.txt
echo inp2 > inp2.txt
echo conv2.txt > subs1.txt
stepup watch-update subs1.txt
stepup watch-delete inp1.txt
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f inp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
[[ -f subs1.txt ]] || exit 1
[[ -f subs2.txt ]] || exit 1
grep conv2.txt subs2.txt
[[ -f subs3.txt ]] || exit 1
grep conv2.txt subs3.txt
