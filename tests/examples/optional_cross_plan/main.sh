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

# The removed sink's own output is cleaned up (this works correctly).
[[ ! -f sub/final.txt ]] || exit 1
# The optional step is no longer needed, so its output must be cleaned up too.
# This currently FAILS: out.txt lingers because the step keeps a stale, elevated
# _implied_need (DEFAULT) even though its only sink was detached in another plan.
[[ ! -f out.txt ]] || exit 1
