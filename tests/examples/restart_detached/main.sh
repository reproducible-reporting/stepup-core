#!/usr/bin/env -S bash -x
source ../example.rc

# Run the plan.
stepup boot -j 1 -w -e & # > current_stdout1.txt &

# Run StepUp for a first time.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f foo.txt ]] || exit 1
[[ -f bar.txt ]] || exit 1

# Run the plan.
rm .stepup/*.log
stepup boot -j 1 -w -e & # > current_stdout2.txt &

# Restart StepUp without making changes.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f foo.txt ]] || exit 1
[[ -f bar.txt ]] || exit 1
