#!/usr/bin/env -S bash -x
source ../example.rc

echo hello > input.txt

# Run 1: full untargeted build on a fresh database.
sb -j 1 -w & # > current_stdout1.txt &
PID=$!

stepup wait
stepup graph current_graph1
stepup join

set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1
[[ -f out.txt ]] || exit 1

# Run 2: target input.txt directly on the resumed database, with plan.py UNCHANGED. input.txt
# is a static file whose creator chain has no PENDING step, so Workflow.reconcile_targets()
# raises a GraphError. This happens in serve(), before the director opens its RPC socket, so
# the director must report a clean ERROR and exit instead of crashing with a traceback. This
# run cannot use `stepup wait`/`stepup join`: there is no socket to connect to, and `get_socket()`
# would just waste `GET_SOCKET_TIMEOUT` seconds before raising, since the socket file never
# appears.
rm .stepup/*.log
sb input.txt -j 1 -w & # > current_stdout2.txt &
PID=$!

set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 2 ]] || exit 1

grep -F "A build target cannot be a static file: input.txt" .stepup/fail.log
