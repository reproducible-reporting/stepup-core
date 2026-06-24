#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
cp plan1.py plan.py
stepup boot -j 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f static/sub/foo.txt ]] || exit 1
[[ -f copy.txt ]] || exit 1

# Remove the file foo.txt and verify the consequences
cp plan2.py plan.py
stepup watch-update plan.py
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f static/sub/foo.txt ]] || exit 1
[[ ! -f copy.txt ]] || exit 1
