#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
stepup boot -n 1 -w & # > current_stdout.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f data/plan.py ]] || exit 1
[[ -f scripts/repeat.py ]] || exit 1
[[ -f data/repeat_single_inp.json ]] || exit 1
[[ -f data/single.txt ]] || exit 1
[[ -f data/multi.txt ]] || exit 1
[[ $(wc -l data/multi.txt | cut -d' ' -f 1) -eq 5 ]] || exit 1
