#!/usr/bin/env -S bash -x
source ../example.rc

# Run the plan.
sb -j 1 -w & # > current_stdout.txt &

# Run StepUp for a first time.
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f line.txt ]] || exit 1
[[ -f log.txt ]] || exit 1
[[ $(wc -l log.txt | cut -d' ' -f 1) -eq 1 ]] || exit 1

# Restart StepUp.
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f line.txt ]] || exit 1
[[ -f log.txt ]] || exit 1
[[ $(wc -l log.txt | cut -d' ' -f 1) -eq 2 ]] || exit 1
