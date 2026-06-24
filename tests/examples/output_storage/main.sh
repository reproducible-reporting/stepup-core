#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example. max_output_size = 64 comes from stepup.toml.
stepup boot -j 1 -w & # > current_stdout1.txt &
PID=$!

stepup wait
stepup graph current_graph1
stepup join

# The ./fail.py step is expected to fail.
[[ -f .stepup/fail.log ]] || exit 1

# The full (untruncated) output is still shown in the terminal: success.log keeps the
# complete 200-byte line from ./big.py, even though only 64 bytes are persisted.
grep -F "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB" .stepup/success.log

# Wait for background processes, if any.
set +e; wait -fn $PID; set -e

# With the director shut down, inspect the persisted output.
./checkdb.py
