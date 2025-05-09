#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example with plan1
cp plan1.py plan.py
stepup boot -n 1 -w & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f hop1.txt ]] || exit 1
[[ -f hop2.txt ]] || exit 1
[[ -f sub/hop3.txt ]] || exit 1


# Run the example with plan1
cp plan2.py plan.py
stepup boot -n 1 -w & # > current_stdout2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f hop1.txt ]] || exit 1
[[ -f hop2.txt ]] || exit 1
[[ -f sub/hop3.txt ]] || exit 1


# Run the example with plan1
cp plan1.py plan.py
stepup -l INFO boot -n 1 -w & # > current_stdout3.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f hop1.txt ]] || exit 1
[[ -f hop2.txt ]] || exit 1
[[ -f sub/hop3.txt ]] || exit 1
