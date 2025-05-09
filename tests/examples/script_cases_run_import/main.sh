#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
cp helper1.py helper.py
stepup boot -n 1 -w & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f first.txt ]] || exit 1
[[ -f second.txt ]] || exit 1
grep "first: one" first.txt
grep "second: one" second.txt


# Restart the example with a different helper
cp helper2.py helper.py
stepup boot -n 1 -w & # > current_stdout2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f first.txt ]] || exit 1
[[ -f second.txt ]] || exit 1
grep "first: other" first.txt
grep "second: other" second.txt
