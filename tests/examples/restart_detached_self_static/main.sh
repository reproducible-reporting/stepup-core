#!/usr/bin/env -S bash -x
source ../example.rc

# Run the plan with the sub directory included.
cp plan1.py plan.py
sb -j 1 -w & # > current_stdout1.txt &

# Wait for StepUp to complete
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f sub/data.txt ]] || exit 1
[[ -f sub/copy.txt ]] || exit 1

# Restart with the sub directory dropped from the plan, which cleans up its outputs.
cp plan2.py plan.py
rm .stepup/*.log
sb -j 1 -w & # > current_stdout2.txt &

# Wait for StepUp to complete
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f sub/data.txt ]] || exit 1
[[ ! -f sub/copy.txt ]] || exit 1

# Restart with the sub directory included again. All its outputs must be reproduced.
cp plan1.py plan.py
rm .stepup/*.log
sb -j 1 -w & # > current_stdout3.txt &

# Wait for StepUp to complete
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f sub/data.txt ]] || exit 1
[[ -f sub/copy.txt ]] || exit 1
