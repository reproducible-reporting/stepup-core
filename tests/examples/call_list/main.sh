#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example.
sb -j 1 -w & # > current_stdout.txt &
PID=$!

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Check that no failures occurred and the expected output is present.
[[ ! -f .stepup/fail.log ]] || exit 1
grep -F './work.py plan '"'"'{"inp": null, "config": "default"}'"'" .stepup/success.log
grep -F './work.py run '"'"'{"inp": null, "out": null}'"'" .stepup/success.log

# Wait for background processes, if any.
set +e; wait -fn $PID; RETURNCODE=$?; set -e
[[ "${RETURNCODE}" -eq 0 ]] || exit 1
