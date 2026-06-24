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
[[ -f hello.txt ]] || exit 1

# Mofify hello.txt externally to StepUp
echo changed > hello.txt

# Call stepup clean to remove all outputs, which should skip hello.txt
stepup clean --all --commit > current_cleanup_1.txt

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f hello.txt ]] || exit 1

# Call stepup clean to remove all outputs, which should skip hello.txt
stepup clean --all --commit --unsafe > current_cleanup_2.txt

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ ! -f hello.txt ]] || exit 1
