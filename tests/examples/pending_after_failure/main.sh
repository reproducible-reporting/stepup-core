#!/usr/bin/env -S bash -x
source ../example.rc

# "false" fails and never creates broken.txt, so the consumer of broken.txt
# can never run either. With --keep-going, the scheduler does not drain,
# so the pending report is produced and must attribute the consumer to the
# "blocked by failed steps" bucket, not to a dead-end input.
sb -j 1 -w -k & # > current_stdout.txt &
PID=$!

stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq $((RETURN_CODE_FAILED | RETURN_CODE_PENDING)) ]] || exit 1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f broken.txt ]] || exit 1
[[ ! -f final.txt ]] || exit 1
