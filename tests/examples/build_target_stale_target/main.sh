#!/usr/bin/env -S bash -x
source ../example.rc

# Static inputs are created here (instead of tracked in git) so later runs can modify them.
echo a1 > a_input.txt
echo b1 > b_input.txt

# Run 1: full untargeted build, so a.txt and b.txt both exist before the stale-elevation
# scenario below begins.
sb -j 1 -w & # > current_stdout1.txt &

stepup wait
stepup graph current_graph1
stepup join
wait

[[ -f a.txt ]] || exit 1
[[ -f b.txt ]] || exit 1
grep -q a1 a.txt
grep -q b1 b.txt

# Run 2: target a.txt once, leaving a stale _implied_need=TARGET on its step.
rm .stepup/*.log
sb a.txt -j 1 -w & # > current_stdout2.txt &

stepup wait
stepup graph current_graph2
stepup join
wait

# Run 3: stale-elevation demotion. Both a_input.txt and b_input.txt change, but only b.txt is
# targeted this time. Without reconcile_targets() flagging the stale TARGET row left by run 2,
# a.txt's step -- PENDING again because its input changed -- would be dispatched even though it
# is no longer a target.
rm .stepup/*.log
echo a2 > a_input.txt
echo b2 > b_input.txt
sb b.txt -j 1 -w & # > current_stdout3.txt &

stepup wait
stepup graph current_graph3
stepup join
wait

grep -q b2 b.txt
grep -q a1 a.txt # not rebuilt: a.txt is no longer a target
