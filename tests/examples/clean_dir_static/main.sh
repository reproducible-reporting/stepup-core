#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
stepup boot -n 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f sub/inp.txt ]] || exit 1
[[ -f sub/tmp.txt ]] || exit 1
[[ -f sub/out.txt ]] || exit 1

stepup clean sub > current_cleanup.txt

# Get the graph after completion of the pending steps.
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f sub/inp.txt ]] || exit 1
[[ ! -f sub/tmp.txt ]] || exit 1
[[ ! -f sub/out.txt ]] || exit 1
