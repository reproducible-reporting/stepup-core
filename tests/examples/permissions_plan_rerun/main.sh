#!/usr/bin/env -S bash -x
source ../example.rc

# Run the plan with non-executable work.py.
chmod -x sub/plan.py
sb -j 1 -w & # > current_stdout.txt &

# Wait and get graph.
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f sub/plan.py ]] || exit 1
[[ ! -f sub/done.txt ]] || exit 1

# Rerun the plan with executable work.py.
chmod +x sub/plan.py

# Wait and get graph.
stepup watch-update sub/plan.py
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f sub/plan.py ]] || exit 1
[[ -f sub/done.txt ]] || exit 1
