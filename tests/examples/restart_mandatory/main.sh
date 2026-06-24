#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
cp plan1.py plan.py
sb -j 1 -w & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f foo.txt ]] || exit 1
[[ ! -f bar.txt ]] || exit 1

# Remove the static file foo.txt, change the plan and restart
cp plan2.py plan.py
rm .stepup/director.log
sb -j 1 -w -e & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f foo.txt ]] || exit 1
[[ -f bar.txt ]] || exit 1
