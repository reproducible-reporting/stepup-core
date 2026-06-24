#!/usr/bin/env -S bash -x
source ../example.rc

# Run the example
sb -j 1 -w & # > current_stdout.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f ping.txt ]] || exit 1
[[ -f pong.txt ]] || exit 1
[[ -f work.txt ]] || exit 1

# Remove both intermediates and output
rm ping.txt pong.txt work.txt
stepup watch-delete ping.txt
stepup watch-delete pong.txt
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f ping.txt ]] || exit 1
[[ -f pong.txt ]] || exit 1
[[ -f work.txt ]] || exit 1
