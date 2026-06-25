#!/usr/bin/env -S bash -x
source ../example.rc

# Baseline: only the x pair is complete; head_y and head_z are partial matches.
echo "hx" > head_x.txt
echo "tx" > tail_x.txt
echo "hy" > head_y.txt
echo "hz" > head_z.txt

sb -j 1 -w & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

[[ -f plan.py ]] || exit 1
grep 'hx tx' paste_x.txt
[[ ! -f paste_y.txt ]] || exit 1
[[ ! -f paste_z.txt ]] || exit 1

# Add the missing tails while StepUp is down, then restart: the new matches are picked up.
echo "ty" > tail_y.txt
echo "tz" > tail_z.txt
rm .stepup/*.log
sb -j 1 -w & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

[[ -f plan.py ]] || exit 1
grep 'hx tx' paste_x.txt
grep 'hy ty' paste_y.txt
grep 'hz tz' paste_z.txt
