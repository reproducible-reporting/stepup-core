#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the initial plan.
cp plan1.py plan.py
stepup boot -n 1 -w & # > current_stdout.txt &

# Initial graph
stepup wait
stepup graph current_graph1

# Remove plan and rerun.
rm plan.py
stepup watch-delete plan.py
stepup run
stepup wait
stepup graph current_graph2

# Check files that are expected to be present and/or missing.
# Files should not be removed because of pending steps:
[[ -f copy.txt ]] || exit 1
[[ -f another.txt ]] || exit 1

# Replace plan and rerun.
cp plan2.py plan.py
stepup watch-update plan.py
stepup run
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f original.txt ]] || exit 1
[[ ! -f copy.txt ]] || exit 1
[[ -f another.txt ]] || exit 1
[[ -f between.txt ]] || exit 1
