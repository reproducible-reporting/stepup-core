#!/usr/bin/env -S bash -x
source ../example.rc

echo v0 > trigger.txt

# Run the example
sb -j 1 -w --reschedule-cap=3 & # > current_stdout.txt &
PID=$!

# Dispatch 1: never.txt missing -> reschedule (count=1), parks PENDING.
stepup wait

# Editing trigger.txt (an ordinary, already-STATIC input) makes X eligible again
# (mark_pending), without ever making the amended never.txt available.
echo v1 > trigger.txt
stepup watch-update trigger.txt
stepup run
stepup wait
# Dispatch 2 -> reschedule (count=2).

echo v2 > trigger.txt
stepup watch-update trigger.txt
stepup run
stepup wait
# Dispatch 3 -> reschedule (count=3, still <= cap).

echo v3 > trigger.txt
stepup watch-update trigger.txt
stepup run
stepup wait
# Dispatch 4 -> count=4 exceeds cap=3, X fails.

stepup graph current_graph
stepup join

# Check that the full exception is printed when exceeding the cap.
grep "stepup.core.api.InputNotFoundError: Amended inputs are not available yet." .stepup/fail.log || exit 1

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 2 ]] || exit 1

[[ ! -f never.txt ]] || exit 1
