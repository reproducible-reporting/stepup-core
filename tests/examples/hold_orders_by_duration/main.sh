#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example. njob=2 leaves exactly one free job slot besides the plan step itself,
# enough to expose the declared-order dispatch bug hold() fixes, if it ever regresses.
sb -j 2 -w & # > current_stdout.txt &

stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# All three children must have run, with the expected content.
[[ -f fast.txt ]] || exit 1
[[ -f medium.txt ]] || exit 1
[[ -f slow.txt ]] || exit 1
grep -qx fast fast.txt
grep -qx medium medium.txt
grep -qx slow slow.txt

# No failures: plan.py's own check (nothing dispatched while held) did not raise.
[[ ! -f .stepup/fail.log ]] || exit 1

# Dispatch order must follow _tail_time DESC (i.e. declared duration, longest first), not
# declaration order (which was fast, medium, slow -- the opposite). START (not SUCCESS) is
# used here because it reflects the scheduler's dispatch decision directly, unaffected by any
# real-time race between concurrently running steps.
line_slow=$(grep -n "START.*work.py slow" .stepup/success.log | head -n 1 | cut -d: -f1)
line_medium=$(grep -n "START.*work.py medium" .stepup/success.log | head -n 1 | cut -d: -f1)
line_fast=$(grep -n "START.*work.py fast" .stepup/success.log | head -n 1 | cut -d: -f1)
[[ "${line_slow}" -lt "${line_medium}" ]] || exit 1
[[ "${line_medium}" -lt "${line_fast}" ]] || exit 1
