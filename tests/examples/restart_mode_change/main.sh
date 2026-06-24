#!/usr/bin/env -S bash -x
source ../example.rc

# Run the plan with the first input.
echo data > input.txt
sb -j 1 -w & # > current_stdout1.txt &

# Wait for StepUp to complete
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f output.txt ]] || exit 1
[[ ! -x output.txt ]] || exit 1
grep data output.txt

# Run the plan with the second input.
chmod +x input.txt
rm .stepup/*.log
sb -j 1 -w & # > current_stdout2.txt &

# Restart StepUp.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f output.txt ]] || exit 1
[[ -x output.txt ]] || exit 1
grep data output.txt
