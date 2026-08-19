#!/usr/bin/env -S bash -x
source ../example.rc

# Prepare a directory outside the project to serve as an absolute static tree.
export mytmpdir=$(mktemp -d)
cleanup() { rm -rf "$mytmpdir"; }
trap cleanup EXIT
echo hello > "$mytmpdir/data.txt"

# Run the example
sb -j 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
# It is not compared against an expected graph:
# the temporary directory differs on every run.
stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f copy.txt ]] || exit 1
grep hello copy.txt
