#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the initial plan.
mkdir -p data/sub
echo hi > data/sub/inp.txt
stepup boot -n 1 -w & # > current_stdout.txt &
PID=$!

# Initial graph
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f data/sub/inp.txt ]] || exit 1
[[ -f data/sub/out.txt ]] || exit 1

# Rename the data directory and rerun
mv data other
stepup watch-delete data/
stepup run
stepup wait
stepup graph current_graph2

# Move back
mv other data
stepup watch-update data/
stepup run
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f data/sub/inp.txt ]] || exit 1
[[ -f data/sub/out.txt ]] || exit 1
