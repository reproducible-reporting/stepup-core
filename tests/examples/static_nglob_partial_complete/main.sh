#!/usr/bin/env -S bash -x
source ../example.rc

# Baseline: only head_x/tail_x form a complete pair; head_y is a partial match.
echo "Not" > head_ignored.txt
echo "matching" > tail_ignored.txt
echo "hx" > head_x.txt
echo "tx" > tail_x.txt
echo "hy" > head_y.txt

sb -j 1 -w & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
[[ -f plan.py ]] || exit 1
[[ ! -f paste_ignored.txt ]] || exit 1
grep 'hx tx' paste_x.txt
[[ ! -f paste_y.txt ]] || exit 1

# Add the missing tail to complete the y pair (creates paste_y). head_z stays partial.
echo "ty" > tail_y.txt
echo "hz" > head_z.txt
stepup watch-update tail_y.txt
stepup watch-update head_z.txt
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

[[ -f plan.py ]] || exit 1
[[ ! -f paste_ignored.txt ]] || exit 1
grep 'hx tx' paste_x.txt
grep 'hy ty' paste_y.txt
[[ ! -f paste_z.txt ]] || exit 1
