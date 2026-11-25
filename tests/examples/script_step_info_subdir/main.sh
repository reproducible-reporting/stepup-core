#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
sb -j 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f data/step_info.json ]] || exit 1
[[ -f work/values.txt ]] || exit 1

# Loading the file converts its workdir back to one relative to the current directory.
python3 - << 'EOF'
import contextlib

from stepup.core.stepinfo import load_step_info

info = load_step_info("data/step_info.json")
assert info.workdir == "work"
with contextlib.chdir("work"):
    assert info.workdir == "."
    assert load_step_info("../data/step_info.json") == info
EOF

# Copy the step info file to the top level for the comparison with the expected output.
cp data/step_info.json current_step_info.json
