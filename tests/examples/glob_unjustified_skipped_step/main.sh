#!/usr/bin/env -S bash -x
source ../example.rc

# First build: the plan step runs and registers the pattern.
sb -j 1 -w -e & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

grep -F "data/a.txt" .stepup/warning.log
[[ -f plan.py ]] || exit 1
[[ -f data/a.txt ]] || exit 1

# Restart with nothing changed: the plan step is skipped, yet the warning must
# still be reported from the persisted nglob rows.
rm .stepup/*.log
sb -j 1 -w -e & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

grep "Ran 0 job" .stepup/success.log
grep -F "data/a.txt" .stepup/warning.log

[[ -f plan.py ]] || exit 1
[[ -f data/a.txt ]] || exit 1
