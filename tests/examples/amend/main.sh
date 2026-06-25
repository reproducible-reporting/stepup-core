#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example. work.py amends inp2.txt as an extra input and out2.txt as an extra
# output while it is running, then copies both inputs to their outputs.
sb -j 1 -w -e & # > current_stdout1.txt &

# Get graph after normal run.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f work.py ]] || exit 1
[[ -f inp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
grep word1 out1.txt
grep word2 out2.txt
