#!/usr/bin/env -S bash -x
source ../example.rc

# Baseline: both the x and y pairs are complete, so paste_x and paste_y are built.
echo "Not" > head_ignored.txt
echo "matching" > tail_ignored.txt
echo "hx" > head_x.txt
echo "tx" > tail_x.txt
echo "hy" > head_y.txt
echo "ty" > tail_y.txt

sb -j 1 -w & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
[[ -f plan.py ]] || exit 1
grep 'hx tx' paste_x.txt
grep 'hy ty' paste_y.txt

# Delete one half of the y pair (breaks the match, removing paste_y).
rm tail_y.txt
stepup watch-delete tail_y.txt
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

[[ -f plan.py ]] || exit 1
grep 'hx tx' paste_x.txt
[[ ! -f paste_y.txt ]] || exit 1
