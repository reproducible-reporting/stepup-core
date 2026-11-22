#!/usr/bin/env -S bash -x
source ../example.rc

# The directory external/ stands in for a remote server, which StepUp does not watch.
mkdir external

# First start: the external data is empty, so the planning step declares the upload step, which runs.
sb -j 1 -w & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

grep -qx "local data" external/data.txt
grep -q 'step:./upload.py' current_graph1.txt

# Second start: the external data agrees with local.txt.
# The planning step runs again and declares no upload step, so it is gone from the graph.
rm .stepup/*.log
sb -j 1 -w & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

grep -q 'START.*check.py' .stepup/success.log
! grep -q 'step:./upload.py' current_graph2.txt || exit 1

# Third start, after the external data was changed by someone else.
# The planning step declares the upload step again, which restores the external data.
echo "external data" > external/data.txt
rm .stepup/*.log
sb -j 1 -w & # > current_stdout3.txt &

stepup wait
stepup join

# Wait for background processes, if any.
wait

grep -q 'START.*upload.py' .stepup/success.log
grep -qx "local data" external/data.txt
