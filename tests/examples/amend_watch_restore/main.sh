#!/usr/bin/env -S bash -x
source ../example.rc

# Baseline run with runtime amendment of inp2.txt/out2.txt.
sb -j 1 -w -e & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
[[ -f plan.py ]] || exit 1
[[ -f work.py ]] || exit 1
[[ -f inp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
grep word1 out1.txt
grep word2 out2.txt

# Edit the managed amended input out of band. Its producer reverts it on the next run,
# so work.py stays skipped.
echo "word2 and some" > inp2.txt
stepup watch-update inp2.txt
stepup run
stepup wait
stepup graph current_graph2
stepup join

# Wait for background processes, if any.
wait

[[ -f plan.py ]] || exit 1
[[ -f work.py ]] || exit 1
[[ -f inp1.txt ]] || exit 1
[[ -f inp2.txt ]] || exit 1
grep word1 out1.txt
grep word2 out2.txt
