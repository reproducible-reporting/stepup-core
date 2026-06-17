#!/usr/bin/env -S bash -x
# Exit on first error and clean up.
set -e
trap 'kill $(pgrep -g $$ | grep -v $$) > /dev/null 2> /dev/null || :' EXIT
rm -rvf $(cat .gitignore)

# Phase 1: the sub-plan consumes ../out.txt, so the optional producer becomes needed.
cp sub/plan1.py sub/plan.py
stepup boot -n 1 -w & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files: the optional producer ran and the consumer ran.
[[ -f out.txt ]] || exit 1
[[ -f sub/final.txt ]] || exit 1

# Phase 2: the sub-plan no longer consumes ../out.txt.
# Only the sub-plan is re-executed; the top-level plan (creator of the optional
# producer) is skipped. The optional producer is now needed by nobody and should
# revert to OPTIONAL, after which out.txt is cleaned up.
cp sub/plan2.py sub/plan.py
stepup boot -n 1 -w & # > current_stdout2.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# The removed consumer's own output is cleaned up (this works correctly).
[[ ! -f sub/final.txt ]] || exit 1
# The optional producer is no longer needed, so its output must be cleaned up too.
# This currently FAILS: out.txt lingers because the producer keeps a stale, elevated
# _implied_need (DEFAULT) even though its only consumer was detached in another plan.
[[ ! -f out.txt ]] || exit 1
