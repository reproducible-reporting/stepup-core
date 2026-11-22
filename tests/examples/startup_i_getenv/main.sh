#!/usr/bin/env -S bash -x
source ../example.rc

# First start.
sb -j 1 -w -e & # > current_stdout1.txt &

stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

grep -qx 1 startup_i.txt

# Second start.
# The step runs again and gets the new value.
rm .stepup/*.log
sb -j 1 -w -e & # > current_stdout2.txt &

stepup wait
stepup join

# Wait for background processes, if any.
wait

grep -q 'START.*getstartup.py' .stepup/success.log
grep -qx 2 startup_i.txt
