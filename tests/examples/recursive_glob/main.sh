#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
stepup boot -n 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
grep 'inp"' current_inp.json
grep 'out"' current_out.json
[[ $(wc -l current_inp.json | cut -d' ' -f1) -eq 8 ]] || exit 1
[[ $(wc -l current_out.json | cut -d' ' -f1) -eq 8 ]] || exit 1
