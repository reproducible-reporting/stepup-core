#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example. njob=2 leaves exactly one free job slot besides the plan step itself,
# enough to expose premature dispatch through a nested hold(), if it ever regresses.
sb -j 2 -w & # > current_stdout.txt &

stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# All three children -- from both nested declare_batch() calls -- must have run.
[[ -f a_fast.txt ]] || exit 1
[[ -f a_slow.txt ]] || exit 1
[[ -f b_medium.txt ]] || exit 1
grep -qx fast a_fast.txt
grep -qx slow a_slow.txt
grep -qx medium b_medium.txt

# No failures: plan.py's own check (nothing dispatched while held) did not raise.
[[ ! -f .stepup/fail.log ]] || exit 1

# Dispatch order must follow _tail_time DESC (i.e. declared duration, longest first), not
# declaration order (which was fast, slow, medium -- fast is declared first, but must run
# last). START (not SUCCESS) is used here because it reflects the scheduler's dispatch
# decision directly, unaffected by any real-time race between concurrently running steps.
line_slow=$(grep -n "START.*work.py slow" .stepup/success.log | head -n 1 | cut -d: -f1)
line_medium=$(grep -n "START.*work.py medium" .stepup/success.log | head -n 1 | cut -d: -f1)
line_fast=$(grep -n "START.*work.py fast" .stepup/success.log | head -n 1 | cut -d: -f1)
[[ "${line_slow}" -lt "${line_medium}" ]] || exit 1
[[ "${line_medium}" -lt "${line_fast}" ]] || exit 1
