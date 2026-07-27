#!/usr/bin/env -S bash -x
source ../example.rc

echo one > a_input.txt
echo two > b_input.txt

# Run 1: full untargeted build.
sb -j 1 -w & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
stepup join
wait

[[ -f out/a.txt ]] || exit 1
[[ -f out/b.txt ]] || exit 1
[[ -f other.txt ]] || exit 1
grep -q one out/a.txt
grep -q one other.txt

# Run 2: startup reconciliation for a directory target (the flagship fix, mirroring
# build_target's Run 3 but for a directory instead of an exact target). a_input.txt
# changes on a resumed database while plan.py stays UNCHANGED, so define_step never
# re-runs for out/a.txt's step. Target only out/: without reconcile_targets()'s bulk
# directory-range UPDATE, this would silently do nothing, since out/a.txt's step's
# persisted _implied_need stays DEFAULT from run 1.
rm .stepup/*.log
echo three > a_input.txt
sb out/ -j 1 -w & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join
wait

grep -q three out/a.txt
grep -q one other.txt # unchanged: other.txt's step was never dispatched, despite its
                       # (shared) input also changing -- it is outside the directory target.
