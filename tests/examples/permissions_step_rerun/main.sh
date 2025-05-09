#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the plan with non-executable work.py.
chmod -x work.py
stepup boot -n 1 -w & # > current_stdout.txt &

# Wait and get graph.
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f work.py ]] || exit 1
[[ ! -f message.txt ]] || exit 1

# Rerun the plan with executable work.py.
chmod +x work.py

# Wait and get graph.
stepup watch-update work.py
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f work.py ]] || exit 1
[[ -f message.txt ]] || exit 1
