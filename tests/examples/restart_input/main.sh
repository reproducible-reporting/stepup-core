#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the plan with the first input.
echo first > input.txt
stepup boot -n 1 -w & # > current_stdout1.txt &

# Wait for StepUp to complete
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f output.txt ]] || exit 1
grep first output.txt

# Run the plan with the second input.
echo second > input.txt
rm .stepup/*.log
stepup boot -n 1 -w & # > current_stdout2.txt &

# Restart StepUp.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f output.txt ]] || exit 1
grep second output.txt
