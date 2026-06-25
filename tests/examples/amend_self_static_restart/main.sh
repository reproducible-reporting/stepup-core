#!/usr/bin/env -S bash -x
source ../example.rc

# Baseline run of the self-declaring work.py.
echo "hello" > input.txt
sb -j 1 -w & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

[[ -f plan.py ]] || exit 1
[[ -f input.txt ]] || exit 1
grep hello output.txt

# Restart without changes. The self-declaring step stays BUILT and does not rerun.
sb -j 1 -w & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

[[ -f plan.py ]] || exit 1
[[ -f input.txt ]] || exit 1
grep hello output.txt
grep BUILT current_graph2.txt
