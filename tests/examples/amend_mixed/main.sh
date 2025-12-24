#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Prepare inputs
echo "Something old" > inp0.txt
echo "First inp1.txt" > inp1.txt
# Prepare a stray output file, which should get overwritten.
# It is intended to confuse the rescheduling.
echo "Stray output" > tmp1.txt

# Run the plan.
stepup boot -n 1 -w -e & # > current_stdout.txt &

# Initial graph
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp0.txt ]] || exit 1
[[ -f inp1.txt ]] || exit 1
[[ -f tmp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
[[ -f log.txt ]] || exit 1

# Remove input and rerun the plan.
rm inp2.txt
stepup watch-delete inp2.txt
stepup run
stepup wait
stepup graph current_graph2

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp0.txt ]] || exit 1
[[ -f inp1.txt ]] || exit 1
[[ -f tmp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
[[ -f log.txt ]] || exit 1

# Replace input and rerun the plan.
echo "Something new" > inp0.txt
echo "Second inp1.txt" > inp1.txt
stepup watch-update inp0.txt
stepup watch-update inp1.txt
stepup run
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp0.txt ]] || exit 1
[[ -f inp1.txt ]] || exit 1
[[ -f tmp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
[[ -f log.txt ]] || exit 1
