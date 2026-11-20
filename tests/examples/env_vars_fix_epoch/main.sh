#!/usr/bin/env -S bash -x
source ../example.rc

# The value of SOURCE_DATE_EPOCH must come from StepUp, not from the environment of the test.
unset SOURCE_DATE_EPOCH

# First start, with the default `--fix-epoch`.
sb -j 1 -w -e & # > current_stdout1.txt &

stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

grep -qx 315532800 epoch_env.txt
grep -qx 315532800 epoch_getenv.txt

# Second start, with `--no-fix-epoch`, which leaves the variable unset.
# Both steps must run again.
rm .stepup/*.log
sb -j 1 -w -e --no-fix-epoch & # > current_stdout2.txt &

stepup wait
stepup join

# Wait for background processes, if any.
wait

grep -q UPDATED .stepup/success.log
grep -qx unset epoch_env.txt
grep -qx unset epoch_getenv.txt

# Third start, with the default `--fix-epoch` again.
# Both steps must run again.
rm .stepup/*.log
sb -j 1 -w -e & # > current_stdout3.txt &

stepup wait
stepup join

# Wait for background processes, if any.
wait

grep -q UPDATED .stepup/success.log
grep -qx 315532800 epoch_env.txt
grep -qx 315532800 epoch_getenv.txt
