#!/usr/bin/env -S bash -x
source ../example.rc

mkdir data
sb -j 1 -w & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

grep "MATCHES: \[\]" .stepup/success.log
[[ -f plan.py ]] || exit 1

# Create the first match and restart. startup.populate_dir_queue must watch data/,
# derived only from the registered pattern (no prior match, no static() declaration).
echo one > data/a.txt
sb -j 1 -w -e & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2

grep "MATCHES: \['data/a.txt'\]" .stepup/success.log

# Without restarting again: data/ was only ever watched because of the glob-derived
# fix under test, so a second live change is picked up only if that watch survived
# the restart.
echo two > data/b.txt
stepup watch-update data/b.txt
stepup run
stepup wait
stepup graph current_graph3
stepup join

# Wait for background processes, if any.
wait

grep "MATCHES: \['data/a.txt', 'data/b.txt'\]" .stepup/success.log

# Check files that are expected to be present and/or missing.
[[ -f plan.py ]] || exit 1
[[ -f data/a.txt ]] || exit 1
[[ -f data/b.txt ]] || exit 1
