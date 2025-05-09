#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run with the initial subs.txt.
echo inp1 > inp1.txt
echo inp1.txt > subs.txt
stepup boot -n 1 -w -e & # > current_stdout.txt &

# Initial graph
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp1.txt ]] || exit 1
[[ -f copy.txt ]] || exit 1
grep inp1 copy.txt

# Change subs.txt and rerun.
rm inp1.txt
echo inp2 > inp2.txt
echo inp2.txt > subs.txt
stepup watch-update subs.txt
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
[[ -f copy.txt ]] || exit 1
grep inp2 copy.txt
