#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the initial plan.
cp -a plan1.py plan.py
stepup boot -n 1 -w & # > current_stdout.txt &

# Initial graph
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f first.txt ]] || exit 1
[[ -f final.txt ]] || exit 1

# Modify the plan.py script and rerun with the modified plan.py.
cp -a plan2.py plan.py
stepup watch-update plan.py
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f first.txt ]] || exit 1
[[ -f second.txt ]] || exit 1
[[ -f final.txt ]] || exit 1
