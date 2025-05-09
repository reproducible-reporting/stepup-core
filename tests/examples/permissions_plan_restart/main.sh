#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the plan with non-executable work.py.
chmod -x sub/plan.py
stepup boot -n 1 -w -e & # > current_stdout1.txt &

# Wait and get graph.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f sub/plan.py ]] || exit 1
[[ -f sub/plan.py ]] || exit 1
[[ ! -f sub/done.txt ]] || exit 1

# Restart the plan with executable work.py.
chmod +x sub/plan.py
rm .stepup/*.log
stepup boot -n 1 -w -e & # > current_stdout2.txt &

# Wait and get graph.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f sub/plan.py ]] || exit 1
[[ -f sub/plan.py ]] || exit 1
[[ -f sub/done.txt ]] || exit 1
