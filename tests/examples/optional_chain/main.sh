#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example without mandatory steps.
cp plan1.py plan.py
stepup boot -n 1 -w -e & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f foo1.txt ]] || exit 1
[[ ! -f foo2.txt ]] || exit 1
[[ ! -f bar.txt ]] || exit 1
[[ ! -f egg.txt ]] || exit 1


# Restart the example with a mandatory step.
cp plan2.py plan.py
rm .stepup/director.log
stepup boot -n 1 -w -e & # > current_stdout2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f foo1.txt ]] || exit 1
[[ -f foo2.txt ]] || exit 1
[[ -f bar.txt ]] || exit 1
[[ -f egg.txt ]] || exit 1
[[ -f spam.txt ]] || exit 1


# Restart the example again without mandatory steps.
cp plan1.py plan.py
rm .stepup/director.log
stepup boot -n 1 -w -e & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f foo1.txt ]] || exit 1
[[ ! -f foo2.txt ]] || exit 1
[[ ! -f bar.txt ]] || exit 1
[[ ! -f egg.txt ]] || exit 1
[[ ! -f spam.txt ]] || exit 1
