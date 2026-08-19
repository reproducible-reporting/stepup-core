#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example. A single "stepup wait" with no intervening "stepup wait -u"/run cycle
# must be enough to converge to SUCCESS: the deferred step here is unfresh-only, which
# self-resolves on the step's own next dispatch, unlike the pre-existing
# unavailable-input deferred path, which needs an external push (mark_pending()).
sb -j 2 -w & # > current_stdout.txt &

stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

[[ -f data.txt ]] || exit 1
grep hello data.txt

# Exactly one unfresh defer, and --- crucially --- none of the pre-existing
# unavailable-input deferred: this build converges purely via the new,
# self-resolving mechanism, without ever touching the old push-based one.
grep "Unfresh dynamic inputs" .stepup/success.log || exit 1
! grep "Unavailable dynamic inputs" .stepup/success.log || exit 1
! grep "Failed command" .stepup/fail.log || exit 1
