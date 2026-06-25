#!/usr/bin/env -S bash -x
source ../example.rc

# Baseline: work.py amends inp.txt as input and out.txt as output.
echo hello > inp.txt
sb -j 1 -w & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
grep hello out.txt

# Delete the amended input and rerun. The step is not rescheduled and out.txt is unaffected.
rm inp.txt
stepup watch-delete inp.txt
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

[[ -f plan.py ]] || exit 1
[[ -f out.txt ]] || exit 1
grep hello out.txt
