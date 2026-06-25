#!/usr/bin/env -S bash -x
source ../example.rc

# Prepare static inputs. Only head_x/tail_x form a complete pair; head_y has no tail,
# and the multi-character "ignored" names never match the single-character placeholder.
echo "Not" > head_ignored.txt
echo "matching" > tail_ignored.txt
echo "hx" > head_x.txt
echo "tx" > tail_x.txt
echo "hy" > head_y.txt

# Run the example
sb -j 1 -w & # > current_stdout1.txt &

# Get the graph after completion of the pending steps.
stepup wait
stepup graph current_graph1
stepup join

# Wait for background processes, if any.
wait

# Only the complete pair matches.
[[ -f plan.py ]] || exit 1
[[ ! -f paste_ignored.txt ]] || exit 1
grep 'hx tx' paste_x.txt
[[ ! -f paste_y.txt ]] || exit 1
