#!/usr/bin/env -S bash -x
source ../example.rc

# The directory remote/ stands in for a remote server, which StepUp does not watch.
mkdir remote
echo first > remote/data.txt

# First start: both steps run.
sb -j 1 -w & # > current_stdout1.txt &

stepup wait
stepup graph current_graph
stepup join

# Wait for background processes, if any.
wait

grep -qx FIRST report.txt

# Second start, with the same remote data.
# The download step runs and leaves data.txt alone, so its consumer is skipped.
rm .stepup/*.log
sb -j 1 -w & # > current_stdout2.txt &

stepup wait
stepup join

# Wait for background processes, if any.
wait

grep -q 'START.*pull.py' .stepup/success.log
! grep -q 'START.*report.txt' .stepup/success.log || exit 1
grep -qx FIRST report.txt

# Third start, after the remote data changed. Both steps run.
echo second > remote/data.txt
rm .stepup/*.log
sb -j 1 -w & # > current_stdout3.txt &

stepup wait
stepup join

# Wait for background processes, if any.
wait

grep -q 'START.*report.txt' .stepup/success.log
grep -qx SECOND report.txt

# Fourth start, with the same remote data as the third. The consumer is skipped again.
rm .stepup/*.log
sb -j 1 -w & # > current_stdout4.txt &

stepup wait
stepup join

# Wait for background processes, if any.
wait

grep -q 'START.*pull.py' .stepup/success.log
! grep -q 'START.*report.txt' .stepup/success.log || exit 1
grep -qx SECOND report.txt
