#!/usr/bin/env -S bash -x
source ../example.rc

# Run the initial plan: the match is not declared static, so a warning fires.
cp plan1.py plan.py
sb -j 1 -w & # > current_stdout.txt &

stepup wait
stepup graph current_graph1
grep -F "data/a.txt" .stepup/warning.log

# Replace the plan with one that declares the match static and rerun: the
# warning disappears once the fixed plan runs, in the same watch session.
cp plan2.py plan.py
stepup watch-update plan.py
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

[[ ! -f .stepup/warning.log ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f data/a.txt ]] || exit 1
