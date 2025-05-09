#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
stepup boot -n 1 -w & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f inp1.txt ]] || exit 1
[[ ! -f out1.txt ]] || exit 1

# Create the first file and restart
echo two > inp2.txt
stepup boot -n 1 -w -e & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp2.txt ]] || exit 1
[[ -f out2.txt ]] || exit 1
grep two out2.txt

# Create the second file and restart
echo three > inp3.txt
stepup boot -n 1 -w -e & # > current_stdout3.txt &

stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp2.txt ]] || exit 1
[[ -f out2.txt ]] || exit 1
grep two out2.txt
[[ -f inp3.txt ]] || exit 1
[[ -f out3.txt ]] || exit 1
grep three out3.txt
