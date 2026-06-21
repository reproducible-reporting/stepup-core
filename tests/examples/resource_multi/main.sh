#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example with two resource types: one slot and two tokens.
echo a slot > s.free
echo token 1 > t1.free
echo token 2 > t2.free
stepup boot -j 3 -w --resources="slot:1,token:2" & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# Check that all steps succeeded.
[[ ! -f .stepup/fail.log ]] || exit 1
[[ ! -f .stepup/warning.log ]] || exit 1
