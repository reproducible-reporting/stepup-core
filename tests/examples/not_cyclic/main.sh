#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
echo "hello" > input.txt
stepup boot -n 1 -w & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f input.txt ]] || exit 1
grep hello output.txt

# Change the input and check that the step reruns without pending dependencies
echo "bye" > input.txt
stepup watch-update input.txt
stepup run
stepup wait
stepup graph current_graph2

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f input.txt ]] || exit 1
grep bye output.txt

# Touch input (no changes) and check that the step reruns without pending dependencies
touch input.txt
rm output.txt
stepup watch-update input.txt
stepup watch-delete output.txt
stepup run
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f input.txt ]] || exit 1
grep bye output.txt

# Do nothig and run again
stepup boot -n 1 -w & # > current_stdout2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph4
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f input.txt ]] || exit 1
grep bye output.txt
grep BUILT current_graph4.txt

echo hello > input.txt
