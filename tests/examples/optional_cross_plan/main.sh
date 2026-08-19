#!/usr/bin/env -S bash -x
source ../example.rc

# Phase 1: the sub-plan consumes ../out.txt, so the optional step becomes needed.
cp sub/plan1.py sub/plan.py
sb -j 1 -w & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files: the optional step ran and its (non-optional) sink ran.
[[ -f out.txt ]] || exit 1
[[ -f sub/final.txt ]] || exit 1

# Phase 2: the sub-plan no longer consumes ../out.txt.
# Only the sub-plan is re-executed; the top-level plan (creator of the optional
# step) is skipped. The optional step is now needed by nobody and should
# revert to OPTIONAL, after which out.txt is cleaned up.
cp sub/plan2.py sub/plan.py
sb -j 1 -w & # > current_stdout2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# The removed sink's own output is cleaned up.
[[ ! -f sub/final.txt ]] || exit 1
[[ ! -f out.txt ]] || exit 1

# Phase 3: the sub-plan consumes ../out.txt again.
# The top-level plan is still not re-executed, so the optional step is not
# redeclared: the only thing that can carry the need back to it is the out.txt
# node that survived the revert in phase 2, with its dependency edge intact.
# Deleting that node on revert would strand the optional step for good.
cp sub/plan1.py sub/plan.py
sb -j 1 -w & # > current_stdout3.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

# The optional step ran again and its sink was rebuilt.
[[ -f out.txt ]] || exit 1
[[ -f sub/final.txt ]] || exit 1
