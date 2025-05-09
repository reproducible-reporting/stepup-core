#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the initial plan.
cp plan_full.py plan.py
cp backup.txt orig.txt
stepup boot -n 1 -w & # > current_stdout.txt &

# First graph
stepup wait
stepup graph current_graph1

# Modify a few things and rerun
cp plan_trimmed.py plan.py
rm orig.txt
stepup watch-update plan.py
stepup watch-delete orig.txt
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f copy1.txt ]] || exit 1
[[ -f copy2.txt ]] || exit 1
