#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
echo a1 > data1.txt
echo b2 > data2.txt
stepup --log-level INFO boot -n 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f work.py ]] || exit 1
[[ -f optional1.txt ]] || exit 1
[[ -f optional2.txt ]] || exit 1
[[ -f work.out ]] || exit 1
grep a1 optional1.txt
grep b2 optional2.txt
grep 'optional 2' work.out

# Modify data.txt and rerun
echo a2 > data2.txt
stepup watch-update data2.txt
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f work.py ]] || exit 1
[[ -f optional1.txt ]] || exit 1
[[ -f optional2.txt ]] || exit 1
[[ -f work.out ]] || exit 1
grep a1 optional1.txt
grep a2 optional2.txt
grep 'optional 2' work.out
