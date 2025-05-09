#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the first plan.
cp plan1.py plan.py
stepup boot -n 1 -w -e & # > current_stdout1.txt &

# Run StepUp for a first time.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan1.py ]] || exit 1
[[ -f copy1.txt ]] || exit 1
[[ -f copy_both1.txt ]] || exit 1
[[ -f source_both.txt ]] || exit 1
[[ -f source1.txt ]] || exit 1

# second with a different plan.
rm .stepup/*.log
cp plan2.py plan.py
stepup boot -n 1 -w -e & # > current_stdout2.txt &

# Restart StepUp.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan1.py ]] || exit 1
[[ -f plan2.py ]] || exit 1

# Static files should never be removed.
[[ -f source_both.txt ]] || exit 1
[[ -f source1.txt ]] || exit 1
[[ -f source2.txt ]] || exit 1

# Only the output file of the second should remain.
[[ ! -f copy1.txt ]] || exit 1
[[ ! -f copy_both1.txt ]] || exit 1
[[ -f copy2.txt ]] || exit 1
[[ -f copy_both2.txt ]] || exit 1
