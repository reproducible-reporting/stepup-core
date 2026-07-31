#!/usr/bin/env -S bash -x
source ../example.rc

# Run the plan for the first time.
sb -j 1 -w -e & # > current_stdout1.txt &

# Run StepUp for the first time.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f src/foo.txt ]] || exit 1
[[ -f copy.txt ]] || exit 1
grep foo copy.txt

# Run the plan again without any changes. If the hand-over from step to tree had gone
# through Trellis.create() instead of the direct creator UPDATE, plan.py's own step hash
# would have been deleted on the first run, and ./plan.py would rerun here instead of
# being skipped.
rm .stepup/*.log
sb -j 1 -w -e & # > current_stdout2.txt &

# Wait for StepUp to complete.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f src/foo.txt ]] || exit 1
[[ -f copy.txt ]] || exit 1
grep foo copy.txt

# The graph must be byte-identical across the restart.
diff current_graph1.txt current_graph2.txt
