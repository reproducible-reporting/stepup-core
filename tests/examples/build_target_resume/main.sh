#!/usr/bin/env -S bash -x
source ../example.rc

# Static input is created here (instead of tracked in git) so later runs can modify it.
echo one > input.txt

# Run 1: full untargeted build.
sb -j 1 -w & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
stepup join
wait

[[ -f wanted.txt ]] || exit 1
[[ -f other.txt ]] || exit 1
grep -q one wanted.txt
grep -q one other.txt

# Run 2: startup reconciliation (the flagship fix, mirroring build_target_dir_resume but for
# an exact target instead of a directory). input.txt (shared by wanted.txt and other.txt)
# changes while plan.py stays UNCHANGED, so define_step never re-runs for either step.
# Target only wanted.txt: without the startup reconciliation pass, this would silently do
# nothing, since wanted.txt's persisted _implied_need stays DEFAULT from run 1.
rm .stepup/*.log
echo two > input.txt
sb wanted.txt -j 1 -w & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join
wait

grep -q two wanted.txt
grep -q one other.txt # unchanged: other.txt's step was never dispatched

# Run 3: recycle path. Edit plan.py (a comment only, no declaration changes) so it re-runs
# and redeclares its children through Step.reattach() instead of a fresh declaration. input.txt
# changes again at the same time, proving that elevation happens via the recycle-triggered
# _check_after flag rather than a fresh call to _declare_file.
rm .stepup/*.log
echo "# trigger a replan without changing any declarations" >> plan.py
echo three > input.txt
sb wanted.txt -j 1 -w & # > current_stdout3.txt &

stepup wait
stepup graph current_graph3
stepup join
wait

grep -q three wanted.txt
grep -q one other.txt # still unchanged
