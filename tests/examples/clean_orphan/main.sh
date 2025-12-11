#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example with plan1
cp plan1.py plan.py
stepup boot -n 1 -w & # > current_stdout_1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph_1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f hello1.txt ]] || exit 1

# Run the example with plan2 and without automatic cleaning
cp plan2.py plan.py
stepup boot -n 1 -w --no-clean & # > current_stdout_2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph_2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f hello1.txt ]] || exit 1
[[ -f hello2.txt ]] || exit 1

# Now clean the orphaned outputs, first dry run
stepup clean > current_cleanup_1.txt

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f hello1.txt ]] || exit 1
[[ -f hello2.txt ]] || exit 1

# Now clean the orphaned outputs, real run
stepup clean --commit > current_cleanup_2.txt

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f hello1.txt ]] || exit 1
[[ -f hello2.txt ]] || exit 1
