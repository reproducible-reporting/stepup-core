#!/usr/bin/env -S bash -x
source ../example.rc

# Run the initial plan.
cp plan_full.py plan.py
cp backup.txt orig.txt
sb -j 1 -w & # > current_stdout.txt &

# First graph
stepup wait
stepup graph current_graph1

# Modify a few things and rerun
cp plan_trimmed.py plan.py
rm orig.txt
stepup watch-update plan.py
stepup watch-delete orig.txt
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f copy1.txt ]] || exit 1
[[ -f copy2.txt ]] || exit 1
