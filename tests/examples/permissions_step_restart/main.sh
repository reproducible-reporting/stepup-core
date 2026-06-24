#!/usr/bin/env -S bash -x
source ../example.rc

# Run the plan with non-executable work.py.
chmod -x work.py
sb -j 1 -w -e & # > current_stdout1.txt &

# Wait and get graph.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f work.py ]] || exit 1
[[ ! -f message.txt ]] || exit 1

# Restart the plan with executable work.py.
chmod +x work.py
rm .stepup/*.log
sb -j 1 -w -e & # > current_stdout2.txt &

# Wait and get graph.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f work.py ]] || exit 1
[[ -f message.txt ]] || exit 1
