#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
export STEPUP_CALL_FORMAT=json
stepup boot -n 1 -w & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f join_inp.json ]] || exit 1
[[ -f join_out.json ]] || exit 1
grep "Hello World" check.txt


# Run the example
export STEPUP_CALL_FORMAT=pickle
stepup boot -n 1 -w & # > current_stdout2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f join_inp.json ]] || exit 1
[[ ! -f join_out.json ]] || exit 1
[[ -f join_inp.pickle ]] || exit 1
[[ -f join_out.pickle ]] || exit 1
grep "Hello World" check.txt
