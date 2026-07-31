#!/usr/bin/env -S bash -x
source ../example.rc

# Phase 1: `a`'s optional step produces hop2.txt, consumed by `b`'s non-optional
# step. hop2.txt is only needed indirectly, so `a`'s optional step's implied need
# must be elevated above OPTIONAL for it to run at all.
echo "hello" > a/hop1.txt
cp a/plan1.py a/plan.py
sb -j 1 -w & # > current_stdout1.txt &
PID=$!

stepup wait
stepup graph current_graph1
stepup join

set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1

[[ -f a/hop2.txt ]] || exit 1
[[ -f b/done.txt ]] || exit 1
grep -qx hello a/hop2.txt
grep -qx hello b/done.txt

# Phase 2: only `a`'s sub-plan reruns (its own plan.py changed); `b`'s sub-plan is
# untouched. `a` redeclares its step with the same command but drops its
# inp/out paths entirely -- see README.txt. A correct implementation must still
# recognize the (now bare) optional step as needed and rerun it, refreshing
# hop2.txt on disk with the current hop1.txt content.
#
# `b`'s own declared input still points at hop2.txt, but since `a` no longer
# declares it as an output, that file node has no producer anymore: `b` is
# correctly left PENDING, reported as blocked on a detached input in the
# "Unavailable inputs" table (the PENDING return code bit, the same code used
# by e.g. the `awaited_detached` example for an incomplete-but-not-crashed
# build) instead of silently reusing a stale result. That part is
# unrelated to the OPTIONAL-step bug under test here, so this phase only checks
# that `a`'s own step reran -- not that `b` completes.
echo "world" > a/hop1.txt
cp a/plan2.py a/plan.py
sb -j 1 -w & # > current_stdout2.txt &
PID=$!

stepup wait
stepup graph current_graph2
stepup join

set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq "${RETURN_CODE_PENDING}" ]] || exit 1

[[ -f a/hop2.txt ]] || exit 1
grep -qx world a/hop2.txt
grep -q "need = DEFAULT (implied by sinks > OPTIONAL)" current_graph2.txt
