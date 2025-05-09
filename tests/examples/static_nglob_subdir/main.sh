#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Run the example
stepup boot -n 1 -w & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f sub/plan.py ]] || exit 1
[[ -f sub/inp1.txt ]] || exit 1
[[ -f sub/inp2.txt ]] || exit 1
[[ -f sub/out1.txt ]] || exit 1
[[ -f sub/out2.txt ]] || exit 1
grep one sub/out1.txt
grep two sub/out2.txt

# Wait for background processes, if any.
wait

# Restart with one more input
echo three > sub/inp3.txt
rm .stepup/*.log
stepup boot -n 1 -w -e & # > current_stdout2.txt &

# Restart StepUp.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f sub/plan.py ]] || exit 1
[[ -f sub/inp1.txt ]] || exit 1
[[ -f sub/inp2.txt ]] || exit 1
[[ -f sub/inp3.txt ]] || exit 1
[[ -f sub/out1.txt ]] || exit 1
[[ -f sub/out2.txt ]] || exit 1
[[ -f sub/out3.txt ]] || exit 1
grep one sub/out1.txt
grep two sub/out2.txt
grep three sub/out3.txt
