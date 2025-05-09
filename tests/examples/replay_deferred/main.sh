#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the initial plan.
echo hello > inp.txt
cp plan1.py plan.py
stepup boot -n 1 -w & # > current_stdout.txt &

# Initial graph
stepup wait
stepup graph current_graph1

# Check the result.
grep hello out.txt

# Take work.py out of the plan.
cp plan2.py plan.py

# Remove input and rerun the plan, should not do much.
stepup watch-update plan.py
stepup run
stepup wait
stepup graph current_graph2

# Check the result, should not be affected.
grep hello out.txt

# Restor work.ppy in the plan.
cp plan1.py plan.py

# Remove input and rerun the plan, should not do much.
stepup watch-update plan.py
stepup run
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp.txt ]] || exit 1
[[ -f out.txt ]] || exit 1
