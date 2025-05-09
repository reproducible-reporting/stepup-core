#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
echo data > input.txt
stepup boot -n 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f input.txt ]] || exit 1
[[ -f output.txt ]] || exit 1
grep data output.txt

# change output, then remove input
echo modified > output.txt
sleep 0.5
rm input.txt
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f input.txt ]] || exit 1
[[ -f output.txt ]] || exit 1
grep modified output.txt

# Call stepup clean to remove all outputs
stepup clean . > current_cleanup.txt

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f input.txt ]] || exit 1
[[ -f output.txt ]] || exit 1
grep modified output.txt
