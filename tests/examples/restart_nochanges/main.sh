#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the plan.
stepup boot -n 1 -w -e & # > current_stdout1.txt &

# Run StepUp for a first time.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f single.txt ]] || exit 1
[[ -f double.txt ]] || exit 1
[[ -f quadruple.txt ]] || exit 1

# Run the plan.
rm .stepup/*.log
stepup boot -n 1 -w -e & # > current_stdout2.txt &

# Restart StepUp without making changes.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f single.txt ]] || exit 1
[[ -f double.txt ]] || exit 1
[[ -f quadruple.txt ]] || exit 1
