#!/usr/bin/env -S bash -x
source ../example.rc

# Run the plan for the first time.
cp plan1.py plan.py
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

# Restart with a different plan.py, so ./plan.py cannot be skipped and redoes its
# declarations on a graph that already holds the tree and the file it owns. Registering
# the tree re-adopts the (detached) file node, after which the static() call naming that
# file must be a no-op instead of recreating an already attached node.
cp plan2.py plan.py
rm .stepup/*.log
sb -j 1 -w -e & # > current_stdout3.txt &

# Wait for StepUp to complete.
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f src/foo.txt ]] || exit 1
[[ -f copy.txt ]] || exit 1
grep foo copy.txt

# Only the declaration order changed, so expected_graph3.txt is expected_graph1.txt
# again. (The current_graph*.txt files cannot be diffed here: they still hold the hashes
# of plan.py, which the test builder strips before comparing with the expected output.)

# Switch back to the original plan, which reruns ./plan.py once more. This repeats the
# hand-over of the first run, now with the tree and the file already in the graph.
cp plan1.py plan.py
rm .stepup/*.log
sb -j 1 -w -e & # > current_stdout4.txt &

# Wait for StepUp to complete.
stepup wait
stepup graph current_graph4
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f src/foo.txt ]] || exit 1
[[ -f copy.txt ]] || exit 1
grep foo copy.txt

# Same graph as ever, see the comment on expected_graph3.txt above.
