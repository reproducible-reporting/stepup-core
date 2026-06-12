#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Prepare input and directories
mkdir -p dir_work dir_inp dir_out dir_vol
echo blabla > dir_inp/inp.txt

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
grep blabla dir_out/out.txt
grep blabla dir_vol/vol.txt
