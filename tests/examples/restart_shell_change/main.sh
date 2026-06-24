#!/usr/bin/env -S bash -x
source ../example.rc

# Run the plan.
echo "hello" > input.txt
cp plan1.py plan.py
sb -j 1 -w -e & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f output.txt ]] || exit 1

# Restart StepUp with shell=False instead of shell=True.
rm .stepup/*.log
cp plan2.py plan.py
sb -j 1 -w -e & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f output.txt ]] || exit 1
