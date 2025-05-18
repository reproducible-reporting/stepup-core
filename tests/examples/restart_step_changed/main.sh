#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the plan.
cp plan1.py plan.py
stepup boot -n 1 -w -e & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f original.txt ]] || exit 1
grep "1" original.txt
[[ -f copy.txt ]] || exit 1
grep "1" copy.txt

# Restart StepUp after removing the output.
rm .stepup/*.log
cp plan2.py plan.py
stepup boot -n 1 -w -e & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f original.txt ]] || exit 1
grep "2" original.txt
[[ -f copy.txt ]] || exit 1
grep "2" copy.txt
