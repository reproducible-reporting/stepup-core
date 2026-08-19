#!/usr/bin/env -S bash -x
source ../example.rc

# Phase 1: build "cached" once, outside any hold(), so it gets a stored hash.
cp plan1.py plan.py
sb -j 2 -w & # > current_stdout.txt &

stepup wait
stepup graph current_graph1

[[ -f cached.txt ]] || exit 1
grep -qx cached cached.txt

# Phase 2: redeclare "cached" (unchanged) next to a new, slow "rerun" sibling, both
# inside a hold() block. plan.py itself checks (while still holding) that "cached" was
# already skipped and "rerun" was not yet dispatched; see plan2.py.
cp plan2.py plan.py
stepup wait -u plan.py
stepup rebuild

stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Both outputs must be present with the expected content.
[[ -f cached.txt ]] || exit 1
[[ -f rerun.txt ]] || exit 1
grep -qx cached cached.txt
grep -qx rerun rerun.txt

# No failures: plan2.py's own in-hold check did not raise.
[[ ! -f .stepup/fail.log ]] || exit 1

# The "cached" SKIP must appear before the "rerun" START in the log, confirming
# SELECT_NEXT_STEP's hash-checkable bypass let it dispatch ahead of release().
line_skip=$(grep -n "SKIP.*work.py cached" .stepup/success.log | tail -n 1 | cut -d: -f1)
line_start=$(grep -n "START.*work.py --sleep" .stepup/success.log | head -n 1 | cut -d: -f1)
[[ "${line_skip}" -lt "${line_start}" ]] || exit 1
