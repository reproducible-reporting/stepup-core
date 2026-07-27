#!/usr/bin/env -S bash -x
source ../example.rc

echo v0 > trigger.txt

# Run the example
sb -j 1 -w --postpone-cap=1 & # > current_stdout.txt &
PID=$!

# Dispatch 1: never.txt missing -> postpone (count=1, == cap), parks PENDING.
stepup wait

# Editing trigger.txt (an ordinary, already-STATIC input) makes X eligible again
# (mark_pending), without ever making the amended never.txt available.
echo v1 > trigger.txt
stepup watch-update trigger.txt
stepup run
stepup wait
# Dispatch 2 -> count=2 exceeds cap=1, X fails.

stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 18 ]] || exit 1

[[ ! -f never.txt ]] || exit 1

# The FAIL report must explain *why* the step failed, even though the step's own
# exit code is 0 (the swallowed InputNotFoundError does not make ./work.py exit non-zero).
grep "Postponed more than 1 times" .stepup/fail.log || exit 1
