#!/usr/bin/env -S bash -x
source ../example.rc

# `result` is declared as a step output while it does not yet exist, so the plan-time
# `_check_no_directories` check (which only rejects paths that are already a directory,
# or spelled with a trailing separator) cannot catch it. The step's own command turns
# `result` into a directory only once it runs. Hashing a directory output raises
# `HashFailedError`, which must fail just this one step, not crash the whole director.
sb -j 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -d result ]] || exit 1
grep -q "Hash computation failed: File digests of directories are not supported: result" \
    .stepup/fail.log
