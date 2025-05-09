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
[[ -f first.txt ]] || exit 1
[[ -f second.txt ]] || exit 1

# Start stepup without checking expected output because watchdog file

# order is not reproducible.
rm .stepup/*.log
stepup boot -n 1 -w &

# Wait for watch phase.
stepup wait

# Test stepup clean that has no effect
stepup clean plan.py

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f first.txt ]] || exit 1
[[ -f second.txt ]] || exit 1

# Test stepup clean that removes first and second
stepup clean ./

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f first.txt ]] || exit 1
[[ ! -f second.txt ]] || exit 1

# Exit stepup
stepup join

# Wait for background processes, if any.
wait
