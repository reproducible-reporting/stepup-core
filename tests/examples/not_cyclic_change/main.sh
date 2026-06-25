#!/usr/bin/env -S bash -x
source ../example.rc

# Baseline run of the self-declaring step.
echo "hello" > input.txt
sb -j 1 -w & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
[[ -f plan.py ]] || exit 1
[[ -f input.txt ]] || exit 1
grep hello output.txt

# Change the input. The step reruns without spurious pending dependencies.
echo "bye" > input.txt
stepup watch-update input.txt
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

[[ -f plan.py ]] || exit 1
[[ -f input.txt ]] || exit 1
grep bye output.txt

# Restore the source file for clean local reruns.
echo hello > input.txt
