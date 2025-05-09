#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the plan with the first input.
echo a > inp1.txt
echo a > inp2.txt
echo a > inp3.txt
export LEVEL=1
stepup boot -n 1 -w & # > current_stdout1.txt &

# Wait for StepUp to complete
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f inp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
[[ -f inp3.txt ]] || exit 1
[[ -f out1.txt ]] || exit 1
[[ -f out2.txt ]] || exit 1
[[ -f out3.txt ]] || exit 1
grep 'level=1' out1.txt
grep 'level=1' out2.txt
grep 'level=1' out3.txt

# Run the plan with the second input.
export LEVEL=2
echo b > inp1.txt
echo b > inp3.txt
rm .stepup/*.log
stepup boot -n 1 -w & # > current_stdout2.txt &

# Wait for StepUp to complete
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f plan.py ]] || exit 1
[[ -f inp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
[[ -f inp3.txt ]] || exit 1
[[ -f out1.txt ]] || exit 1
[[ ! -f out2.txt ]] || exit 1
[[ ! -f out3.txt ]] || exit 1
grep 'level=2' out1.txt
