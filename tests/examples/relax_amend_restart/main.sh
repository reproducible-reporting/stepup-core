#!/usr/bin/env -S bash -x
source ../example.rc

echo v0 > trigger.txt

# Phase 1: fresh director. producer.sh and consumer.py are dispatched concurrently
# (-j 2), so consumer.py's first amend() call is unfresh; it reschedules once and then
# converges.
sb -j 2 -w & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

[[ -f data.txt ]] || exit 1
grep hello data.txt
grep "Unfresh amended inputs" .stepup/warning.log || exit 1

# Phase 2: restart the director (fresh Scheduler => empty start_times/stop_times).
# Force consumer.py to re-attempt via an edit to its ordinary (non-amended) input,
# without touching data.txt or producer.sh at all --- producer.sh stays SUCCEEDED and
# is never re-dispatched this invocation, so it has no stop_times entry whatsoever.
# The freshness check must therefore be skipped entirely (no entry --> treated as
# fresh), not spuriously block the step.
echo v1 > trigger.txt
rm .stepup/*.log
sb -j 2 -w & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

[[ -f data.txt ]] || exit 1
grep hello data.txt
! grep RESCHEDULE .stepup/warning.log || exit 1
! grep "Failed command" .stepup/fail.log || exit 1
