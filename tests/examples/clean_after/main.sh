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
[[ -f plan.py ]] || exit 1
[[ -d sub/ ]] || exit 1
[[ -f sub/inp.txt ]] || exit 1
[[ -f sub/out.txt ]] || exit 1

# Now clean the outputs in sub/, adding an unknown file to test that it is not removed.
touch sub/unknown
mkdir other
(cd other; STEPUP_ROOT=".." stepup clean ../sub/ --all --commit > ../current_cleanup.txt)

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -d sub/ ]] || exit 1
[[ -f sub/unknown ]] || exit 1
[[ ! -f sub/inp.txt ]] || exit 1
[[ ! -f sub/out.txt ]] || exit 1
