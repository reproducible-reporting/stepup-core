#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example with the input in place.
mkdir sub
echo hello > sub/inp.txt
sb -j 1 -w & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f sub/inp.txt ]] || exit 1
[[ -f out.txt ]] || exit 1

# Remove the input, its directory and the output, then restart.
rm -rf sub out.txt
sb -j 1 -w -e & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2

# The input is missing and StepUp does not recreate the directory it belongs in.
[[ ! -d sub ]] || exit 1
[[ ! -f out.txt ]] || exit 1

# Restore the directory and the input, which the watcher must still pick up.
mkdir sub
echo hello > sub/inp.txt
stepup watch-update sub/inp.txt
stepup run
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f sub/inp.txt ]] || exit 1
[[ -f out.txt ]] || exit 1
grep hello out.txt
