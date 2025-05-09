#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the initial plan.
cp first.txt input.txt
stepup boot -n 1 -w & # > current_stdout.txt &

# Initial graph
stepup wait
stepup graph current_graph1

# Remove input and rerun the plan.
rm input.txt
stepup watch-delete input.txt
stepup run
stepup wait
stepup graph current_graph2

# Replace input and rerun the plan.
cp second.txt input.txt
stepup watch-update input.txt
stepup run
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f input.txt ]] || exit 1
[[ -f output.txt ]] || exit 1
